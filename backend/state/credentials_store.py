"""Encrypted broker credential storage.

Canonical source: Postgres ``user_credentials`` table (Schema.md §2.1).
Runtime cache: Redis ``user:credentials:{broker}`` (keys.py).

Encryption: AES-256-GCM with a 96-bit random nonce.
Key: ``Settings.creds_encryption_key`` (base64 of 32 raw bytes).
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from state.config_loader import get_settings
from state.keys import USER_CREDENTIALS_UPSTOX

# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

_NONCE_SIZE: int = 12
_KEY_SIZE: int = 32

_test_key: bytes | None = None


def _derive_key(b64_key: str) -> bytes:
    """Decode a base64 (standard or url-safe) 32-byte AES key."""
    b64_key = b64_key.strip()
    for alt in (b64_key, b64_key + "=", b64_key + "=="):
        try:
            decoded = base64.urlsafe_b64decode(alt)
            if len(decoded) == _KEY_SIZE:
                return decoded
        except Exception:
            pass
    try:
        decoded = base64.b64decode(b64_key)
        if len(decoded) == _KEY_SIZE:
            return decoded
    except Exception:
        pass
    raise ValueError(
        f"CREDS_ENCRYPTION_KEY must be base64 of 32 bytes; got length hint {len(b64_key)}"
    )


def _get_aesgcm() -> AESGCM:
    """Return an AESGCM instance using the test key (if set) or env key."""
    if _test_key is not None:
        return AESGCM(_test_key)
    raw_key = _derive_key(get_settings().creds_encryption_key)
    return AESGCM(raw_key)


def encrypt_blob(plaintext: bytes) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM; return ``nonce || ciphertext``."""
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = _get_aesgcm().encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_blob(blob: bytes) -> bytes:
    """Decrypt ``nonce || ciphertext`` with AES-256-GCM."""
    if len(blob) < _NONCE_SIZE:
        raise ValueError("encrypted blob too short")
    nonce = blob[:_NONCE_SIZE]
    ciphertext = blob[_NONCE_SIZE:]
    return _get_aesgcm().decrypt(nonce, ciphertext, None)


def encrypt_json(data: dict[str, Any]) -> bytes:
    """Serialize *data* to JSON and encrypt."""
    return encrypt_blob(json.dumps(data, separators=(",", ":")).encode("utf-8"))


def decrypt_json(blob: bytes) -> dict[str, Any]:
    """Decrypt *blob* and parse JSON."""
    return json.loads(decrypt_blob(blob).decode("utf-8"))


# ---------------------------------------------------------------------------
# Postgres CRUD
# ---------------------------------------------------------------------------

async def read_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    broker: str,
) -> dict[str, Any] | None:
    """Read and decrypt credentials for *broker* from Postgres.

    Returns ``None`` if the row does not exist.
    """
    sql = "SELECT encrypted_value FROM user_credentials WHERE broker = $1"
    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            row = await conn.fetchrow(sql, broker)
    else:
        row = await pool_or_conn.fetchrow(sql, broker)
    if row is None:
        return None
    return decrypt_json(row["encrypted_value"])


async def write_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    broker: str,
    data: dict[str, Any],
) -> None:
    """Encrypt *data* and upsert into Postgres ``user_credentials``."""
    encrypted = encrypt_json(data)
    sql = """
        INSERT INTO user_credentials (broker, encrypted_value)
        VALUES ($1, $2)
        ON CONFLICT (broker) DO UPDATE
        SET encrypted_value = EXCLUDED.encrypted_value,
            updated_at = now()
    """
    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            await conn.execute(sql, broker, encrypted)
    else:
        await pool_or_conn.execute(sql, broker, encrypted)


async def delete_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    broker: str,
) -> bool:
    """Delete credentials for *broker*.  Returns ``True`` if a row was removed."""
    sql = "DELETE FROM user_credentials WHERE broker = $1"
    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            result = await conn.execute(sql, broker)
    else:
        result = await pool_or_conn.execute(sql, broker)
    return result == "DELETE 1"


# ---------------------------------------------------------------------------
# Redis sync
# ---------------------------------------------------------------------------

def _redis_key(broker: str) -> str:
    if broker == "upstox":
        return USER_CREDENTIALS_UPSTOX
    return f"user:credentials:{broker}"


async def sync_to_redis(
    redis: Any,  # redis.asyncio.Redis
    broker: str,
    data: dict[str, Any],
) -> None:
    """Write decrypted credential JSON to the canonical Redis key."""
    await redis.set(_redis_key(broker), json.dumps(data, separators=(",", ":")))


async def delete_from_redis(redis: Any, broker: str) -> None:
    """Remove credential key from Redis."""
    await redis.delete(_redis_key(broker))


# ---------------------------------------------------------------------------
# Masking (for GET /credentials/upstox)
# ---------------------------------------------------------------------------

def mask_value(value: str | None, keep: int = 4) -> str | None:
    """Mask a string, revealing only the last *keep* characters.

    Returns ``None`` when input is ``None``.
    """
    if value is None:
        return None
    if len(value) <= keep + 2:
        return "****"
    return f"****{value[-keep:]}"


def mask_credentials(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with sensitive fields masked.

    Fields masked: ``api_key``, ``api_secret``, ``totp_key``,
    ``analytics_token``, ``sandbox_token``, ``pin``.
    ``mobile_no`` is masked to the last 4 digits.
    ``redirect_uri`` is left visible.
    """
    out = dict(data)
    for field in (
        "api_key",
        "api_secret",
        "totp_key",
        "analytics_token",
        "sandbox_token",
        "pin",
    ):
        if field in out:
            out[field] = mask_value(out[field])
    if "mobile_no" in out and isinstance(out["mobile_no"], str):
        mn = out["mobile_no"]
        out["mobile_no"] = (
            f"{'*' * max(0, len(mn) - 4)}{mn[-4:]}" if len(mn) > 4 else "****"
        )
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REQUIRED_UPSTOX_FIELDS: frozenset[str] = frozenset(
    {"api_key", "api_secret", "redirect_uri", "totp_key", "mobile_no", "pin"}
)


def validate_upstox_payload(data: dict[str, Any]) -> None:
    """Raise ``ValueError`` if required Upstox fields are missing."""
    missing = REQUIRED_UPSTOX_FIELDS - data.keys()
    if missing:
        raise ValueError(f"missing required Upstox fields: {sorted(missing)}")


# ---------------------------------------------------------------------------
# Convenience: full round-trip
# ---------------------------------------------------------------------------

async def persist_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
    broker: str,
    data: dict[str, Any],
) -> None:
    """Encrypt to Postgres and write decrypted copy to Redis."""
    await write_credentials(pool_or_conn, broker, data)
    await sync_to_redis(redis, broker, data)


async def load_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
    broker: str,
) -> dict[str, Any] | None:
    """Read from Postgres and refresh Redis cache."""
    data = await read_credentials(pool_or_conn, broker)
    if data is not None:
        await sync_to_redis(redis, broker, data)
    return data


async def clear_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
    broker: str,
) -> None:
    """Delete from Postgres and Redis."""
    await delete_credentials(pool_or_conn, broker)
    await delete_from_redis(redis, broker)


# ---------------------------------------------------------------------------
# Bootstrap from local JSON file (one-off operator script)
# ---------------------------------------------------------------------------

def read_local_credentials_file(path: str = "credentials.json") -> dict[str, Any] | None:
    """Read the local (git-ignored) ``credentials.json`` and return the
    ``upstox`` entry if present.

    This is intended for one-time operator bootstrap only; the canonical
    store is Postgres ``user_credentials``.
    """
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw.get("upstox")


async def bootstrap_from_file(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
    path: str = "credentials.json",
) -> bool:
    """Import ``upstox`` credentials from *path* into Postgres + Redis.

    Returns ``True`` if imported, ``False`` if file or ``upstox`` key missing.
    """
    upstox = read_local_credentials_file(path)
    if upstox is None:
        return False
    validate_upstox_payload(upstox)
    await persist_credentials(pool_or_conn, redis, "upstox", upstox)
    return True


# ---------------------------------------------------------------------------
# Init-engine / hydrator helpers
# ---------------------------------------------------------------------------

async def hydrate_credentials_to_redis(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
) -> dict[str, Any] | None:
    """Read Upstox credentials from Postgres and write to Redis.

    Returns the decrypted dict on success, ``None`` if row missing.
    Used by ``postgres_hydrator.hydrate_credentials`` (TDD §3.3).
    """
    data = await read_credentials(pool_or_conn, "upstox")
    if data is not None:
        await sync_to_redis(redis, "upstox", data)
    return data


async def init_engine_load_credentials(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    redis: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load credentials during Init Step 6 (Sequential_Flow.md §7).

    Returns ``(data, error_reason)``.  *error_reason* is ``None`` on
    success, or one of ``"missing"`` / ``"decrypt_failed"`` on failure.
    """
    try:
        data = await read_credentials(pool_or_conn, "upstox")
    except Exception:
        return None, "decrypt_failed"
    if data is None:
        return None, "missing"
    await sync_to_redis(redis, "upstox", data)
    return data, None


# ---------------------------------------------------------------------------
# Test injection helpers
# ---------------------------------------------------------------------------

def set_test_encryption_key(key_b64: str) -> None:
    """Override the encryption key for tests."""
    global _test_key
    _test_key = _derive_key(key_b64)


def reset_test_encryption_key() -> None:
    """Clear the test key override."""
    global _test_key
    _test_key = None


# ---------------------------------------------------------------------------
# Standalone CLI entrypoint (async)
# ---------------------------------------------------------------------------

async def _cli_main() -> int:
    """Read ``credentials.json`` and upsert into Postgres + Redis."""
    from state.postgres_client import init_pool, get_pool
    from state.redis_client import init_pools, get_redis

    init_pools()
    await init_pool()

    pg_pool = get_pool()
    redis = get_redis()

    imported = await bootstrap_from_file(pg_pool, redis)
    if imported:
        print("Credentials imported from credentials.json → Postgres + Redis")
        return 0
    print("No credentials.json or no 'upstox' key found")
    return 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_cli_main()))
