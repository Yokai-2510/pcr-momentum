"""
engines.init.postgres_hydrator — Postgres -> Redis at boot (Init step 4).

Mirrors `user_*`, `config_*`, `market_calendar` rows into the runtime Redis
namespaces so the rest of the engines can read everything from Redis without
any further Postgres round-trips during the trading day.

Refs:
  - Sequential_Flow.md §7 STEP 4 (Hydrate from Postgres)
  - Schema.md §1.2 / §1.4 (Redis surface) and §2 (Postgres surface)
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import asyncpg
import orjson
import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K
from state.config_loader import load_runtime_configs, redis_key_for_config
from state.crypto import decrypt_json


async def hydrate_user_account(
    redis: _redis_async.Redis, conn: asyncpg.Connection
) -> dict[str, Any] | None:
    """Mirror the single admin user_accounts row into user:account:* Redis keys."""
    row = await conn.fetchrow(
        "SELECT id, username, jwt_secret, role FROM user_accounts ORDER BY created_at LIMIT 1"
    )
    if row is None:
        logger.warning("hydrate_user_account: no user row found")
        return None
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.USER_ACCOUNT_USERNAME, row["username"])
    pipe.set(K.USER_ACCOUNT_JWT_SECRET, row["jwt_secret"])
    pipe.set(K.USER_ACCOUNT_ROLE, row["role"] or "admin")
    await pipe.execute()
    return {"username": row["username"], "role": row["role"], "user_id": str(row["id"])}


async def hydrate_credentials(redis: _redis_async.Redis, conn: asyncpg.Connection) -> bool:
    """Decrypt user_credentials.upstox row and mirror to Redis.

    Returns:
        True   — row found AND decrypted successfully
        False  — row missing OR decryption failed (caller sets
                 system:flags:trading_disabled_reason = "awaiting_credentials")
    """
    row = await conn.fetchrow(
        "SELECT encrypted_value FROM user_credentials WHERE broker = $1", "upstox"
    )
    if row is None:
        logger.warning("hydrate_credentials: no upstox row in user_credentials")
        await redis.set(K.SYSTEM_HEALTH_AUTH, "missing")
        return False
    try:
        creds = decrypt_json(bytes(row["encrypted_value"]))
    except Exception as e:
        logger.error(f"hydrate_credentials: decrypt failed: {e}")
        await redis.set(K.SYSTEM_HEALTH_AUTH, "missing")
        return False
    await redis.set(K.USER_CREDENTIALS_UPSTOX, orjson.dumps(creds))
    return True


async def hydrate_configs(redis: _redis_async.Redis, conn: asyncpg.Connection) -> int:
    """Mirror all config_settings rows into the strategy:configs:* Redis keys.

    Returns the number of rows mirrored.
    """
    raw = await load_runtime_configs(conn)
    pipe = redis.pipeline(transaction=False)
    written = 0
    for cfg_key, value in raw.items():
        try:
            redis_key = redis_key_for_config(cfg_key)
        except KeyError:
            logger.warning(f"hydrate_configs: skipping unknown config key {cfg_key!r}")
            continue
        pipe.set(redis_key, orjson.dumps(value))
        written += 1
    await pipe.execute()
    return written


async def hydrate_market_calendar(
    redis: _redis_async.Redis, conn: asyncpg.Connection
) -> dict[str, int]:
    """Populate `system:scheduler:market_calendar:trading_days` and `:holidays`
    from the `market_calendar` table.

    For dates >= today, write each into the corresponding SET.
    """
    rows = await conn.fetch(
        "SELECT date, is_trading FROM market_calendar WHERE date >= $1 ORDER BY date",
        date.today(),
    )
    trading: list[str] = []
    holidays: list[str] = []
    for r in rows:
        iso = r["date"].isoformat()
        if r["is_trading"]:
            trading.append(iso)
        else:
            holidays.append(iso)

    pipe = redis.pipeline(transaction=False)
    if trading:
        pipe.sadd(K.SYSTEM_SCHEDULER_TRADING_DAYS, *trading)
    if holidays:
        pipe.sadd(K.SYSTEM_SCHEDULER_HOLIDAYS, *holidays)
    await pipe.execute()
    return {"trading_days": len(trading), "holidays": len(holidays)}


async def hydrate_scheduler_session(redis: _redis_async.Redis, conn: asyncpg.Connection) -> bool:
    """Mirror today's session timing into `system:scheduler:market_calendar:session`.

    Reads from config_settings.session (fallback) and overlays today's row from
    market_calendar.session if present.
    """
    sess_row = await conn.fetchrow("SELECT value FROM config_settings WHERE key = 'session'")
    session_value: dict[str, Any] = {}
    if sess_row is not None:
        raw = sess_row["value"]
        session_value = json.loads(raw) if isinstance(raw, str) else dict(raw)

    cal_row = await conn.fetchrow(
        "SELECT session FROM market_calendar WHERE date = $1", date.today()
    )
    if cal_row is not None and cal_row["session"]:
        cal = cal_row["session"]
        if isinstance(cal, str):
            cal = json.loads(cal)
        session_value.update(cal)

    if not session_value:
        return False
    # Store as a HASH for direct HGET access by Scheduler/Strategy.
    pipe = redis.pipeline(transaction=False)
    pipe.delete(K.SYSTEM_SCHEDULER_SESSION)
    if session_value:
        pipe.hset(K.SYSTEM_SCHEDULER_SESSION, mapping={k: str(v) for k, v in session_value.items()})
    await pipe.execute()
    return True


async def hydrate_all(redis: _redis_async.Redis, pool: asyncpg.Pool) -> dict[str, Any]:
    """Run every hydrator in sequence inside a single connection.

    Returns a dict of per-step summaries. Caller checks `creds_ok` to decide
    auth bootstrap path.
    """
    summary: dict[str, Any] = {}
    async with pool.acquire() as conn:
        summary["user"] = await hydrate_user_account(redis, conn)
        summary["creds_ok"] = await hydrate_credentials(redis, conn)
        summary["configs"] = await hydrate_configs(redis, conn)
        summary["calendar"] = await hydrate_market_calendar(redis, conn)
        summary["session"] = await hydrate_scheduler_session(redis, conn)
    return summary
