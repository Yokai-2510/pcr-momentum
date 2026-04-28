"""Bootstrap smoke check for the EC2 host.

Verifies that the freshly-provisioned EC2 environment is wired up correctly:
  - .env is readable and contains the expected keys
  - Redis reachable via Unix socket
  - Postgres reachable using DATABASE_URL (raw asyncpg DSN form)
  - CREDS_ENCRYPTION_KEY decodes to 32 bytes
  - JWT_SECRET is non-empty

Usage on EC2:
    python3 /home/ubuntu/premium_diff_bot/scripts/setup/smoke_check.py [--rotate-db]

`--rotate-db` rotates the `trader` Postgres password (requires sudo for `peer`
auth as `postgres`) and rewrites DATABASE_URL in .env. Use this after any
incident where the previous password may have been exposed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import pathlib
import secrets
import string
import subprocess
import sys


ROOT = pathlib.Path("/home/ubuntu/premium_diff_bot")
ENV_FILE = ROOT / ".env"
DB_SECRET_FILE = ROOT / ".db_secret"


def read_env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f".env file not found at {ENV_FILE}")
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key] = value
    return out


def write_env_file(values: dict[str, str]) -> None:
    body = "# Auto-generated during EC2 bootstrap. Keep this file secret.\n"
    body += "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
    ENV_FILE.write_text(body)
    os.chmod(ENV_FILE, 0o600)


def to_asyncpg_dsn(database_url: str) -> str:
    """Strip SQLAlchemy '+asyncpg' suffix if present."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def rotate_db_password(values: dict[str, str]) -> dict[str, str]:
    alphabet = string.ascii_letters + string.digits
    new_pass = "".join(secrets.choice(alphabet) for _ in range(40))
    cmd = [
        "sudo",
        "-u",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        f"ALTER ROLE trader WITH LOGIN PASSWORD '{new_pass}';",
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    DB_SECRET_FILE.write_text(f"DB_PASSWORD={new_pass}\n")
    os.chmod(DB_SECRET_FILE, 0o600)
    values["DATABASE_URL"] = (
        f"postgresql+asyncpg://trader:{new_pass}@127.0.0.1:5432/premium_diff_bot"
    )
    write_env_file(values)
    return values


async def check_postgres(database_url: str) -> tuple[bool, str]:
    try:
        import asyncpg
    except ImportError as e:
        return False, f"asyncpg not installed: {e}"
    try:
        conn = await asyncpg.connect(to_asyncpg_dsn(database_url))
        version = await conn.fetchval("SELECT version();")
        await conn.close()
        return True, f"OK ({version.split(',')[0]})"
    except Exception as e:
        return False, f"FAIL: {e}"


async def check_redis(redis_url: str) -> tuple[bool, str]:
    try:
        import redis.asyncio as aioredis
    except ImportError as e:
        return False, f"redis not installed: {e}"
    try:
        # redis+unix:///var/run/redis/redis.sock?db=0  ->  unix:///var/run/redis/redis.sock
        from_url_target = redis_url.replace("redis+unix://", "unix://", 1)
        client = aioredis.from_url(from_url_target)
        pong = await client.ping()
        await client.aclose()
        return bool(pong), f"OK ({'PONG' if pong else 'no pong'})"
    except Exception as e:
        return False, f"FAIL: {e}"


def check_creds_key(value: str) -> tuple[bool, str]:
    if not value:
        return False, "empty"
    try:
        # urlsafe-b64 may have stripped padding; restore.
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) != 32:
            return False, f"decoded length is {len(raw)} bytes, expected 32"
        return True, "OK (32 bytes)"
    except Exception as e:
        return False, f"decode error: {e}"


def check_jwt_secret(value: str) -> tuple[bool, str]:
    if not value:
        return False, "empty"
    if len(value) < 32:
        return False, f"too short ({len(value)} chars)"
    return True, f"OK ({len(value)} chars)"


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


async def main_async(args: argparse.Namespace) -> int:
    values = read_env_file()
    print("=" * 60)
    print("  premium_diff_bot — EC2 smoke check")
    print("=" * 60)

    expected_keys = {
        "APP_ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "CREDS_ENCRYPTION_KEY",
        "JWT_SECRET",
        "SEED_ADMIN_PASSWORD",
        "UPSTOX_API_KEY",
        "UPSTOX_API_SECRET",
        "UPSTOX_REDIRECT_URI",
        "UPSTOX_TOTP_KEY",
    }
    missing = expected_keys - values.keys()
    if missing:
        print(f"[env]      MISSING keys: {sorted(missing)}")
        return 2
    print(f"[env]      {len(values)} keys loaded from {ENV_FILE}")

    if args.rotate_db:
        print("[postgres] rotating trader password ...")
        values = rotate_db_password(values)
        print("[postgres] rotated; DATABASE_URL updated in .env")

    ok_pg, info_pg = await check_postgres(values["DATABASE_URL"])
    print(f"[postgres] {info_pg}")

    ok_redis, info_redis = await check_redis(values["REDIS_URL"])
    print(f"[redis]    {info_redis}")

    ok_key, info_key = check_creds_key(values["CREDS_ENCRYPTION_KEY"])
    print(f"[creds]    CREDS_ENCRYPTION_KEY: {info_key}")

    ok_jwt, info_jwt = check_jwt_secret(values["JWT_SECRET"])
    print(f"[creds]    JWT_SECRET:           {info_jwt}")

    print("-" * 60)
    print(f"[upstox]   api_key:        {mask(values['UPSTOX_API_KEY'])}")
    print(f"[upstox]   redirect_uri:   {values['UPSTOX_REDIRECT_URI']}")
    print(f"[upstox]   totp_key:       {mask(values['UPSTOX_TOTP_KEY'])}")
    print(f"[seed]     admin_password: {mask(values['SEED_ADMIN_PASSWORD'])}")
    print("=" * 60)

    all_ok = all([ok_pg, ok_redis, ok_key, ok_jwt])
    print("RESULT:", "ALL_OK" if all_ok else "FAILURES_PRESENT")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate-db",
        action="store_true",
        help="Rotate the trader Postgres password and update .env",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
