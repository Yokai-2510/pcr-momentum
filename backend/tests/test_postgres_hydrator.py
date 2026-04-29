"""init.postgres_hydrator — integration test against the live EC2 Postgres.

Skipped automatically if DATABASE_URL isn't set (so this is safe in CI).
On EC2 with the seeded DB, hydrate_all should populate user:account:*,
strategy:configs:*, and the market_calendar SETs.
"""

from __future__ import annotations

import os

import asyncpg
import fakeredis.aioredis as fakeredis_async
import orjson
import pytest

from engines.init import postgres_hydrator
from state import keys as K
from state.config_loader import RUNTIME_CONFIG_REDIS_MAP

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


def _normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


@pytest.fixture
async def pg_pool():
    pool = await asyncpg.create_pool(
        dsn=_normalize_dsn(os.environ["DATABASE_URL"]), min_size=1, max_size=2
    )
    yield pool
    await pool.close()


@pytest.fixture
async def redis():
    r = fakeredis_async.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


async def test_hydrate_user_account(redis, pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        out = await postgres_hydrator.hydrate_user_account(redis, conn)
    assert out is not None
    assert out["username"] == "admin"
    assert (await redis.get(K.USER_ACCOUNT_USERNAME)) == b"admin"
    assert (await redis.get(K.USER_ACCOUNT_ROLE)) == b"admin"


async def test_hydrate_configs_mirrors_all_keys(redis, pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        n = await postgres_hydrator.hydrate_configs(redis, conn)
    assert n == 5  # execution + session + risk + index:nifty50 + index:banknifty
    for cfg_key, redis_key in RUNTIME_CONFIG_REDIS_MAP.items():
        raw = await redis.get(redis_key)
        assert raw is not None, f"missing {redis_key} for cfg {cfg_key}"
        # Sanity: orjson-decodable JSON dict
        parsed = orjson.loads(raw)
        assert isinstance(parsed, dict)


async def test_hydrate_credentials_returns_bool(redis, pg_pool) -> None:
    # We don't assert ok=True because the seeded creds row may be encrypted with
    # a different key in prod. We do assert the function never raises and either
    # path leaves system:health:auth in a known value.
    async with pg_pool.acquire() as conn:
        ok = await postgres_hydrator.hydrate_credentials(redis, conn)
    assert isinstance(ok, bool)
    auth_health = await redis.get(K.SYSTEM_HEALTH_AUTH)
    if not ok:
        assert auth_health == b"missing"
    else:
        # Successful decrypt populates the credentials JSON
        creds = orjson.loads(await redis.get(K.USER_CREDENTIALS_UPSTOX))
        assert "api_key" in creds


async def test_hydrate_all_runs_clean(redis, pg_pool) -> None:
    out = await postgres_hydrator.hydrate_all(redis, pg_pool)
    assert "creds_ok" in out and "configs" in out and "calendar" in out
