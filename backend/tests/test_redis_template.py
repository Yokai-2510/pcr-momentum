"""init.redis_template — apply + flush_runtime_namespaces."""

from __future__ import annotations

import fakeredis.aioredis as fakeredis_async
import orjson
import pytest

from engines.init import redis_template
from state import keys as K


@pytest.fixture
async def redis():
    r = fakeredis_async.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


async def test_apply_writes_canonical_defaults(redis) -> None:
    out = await redis_template.apply(redis, flush_runtime=False)
    assert out["written"] > 0

    # Spot-check a few key types from each namespace
    assert (await redis.get(K.SYSTEM_FLAGS_READY)) == b"false"
    assert (await redis.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)) == b"false"
    assert (await redis.get(K.SYSTEM_FLAGS_MODE)) == b"paper"
    assert (await redis.get(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON)) == b"none"
    assert (await redis.get(K.MARKET_DATA_INSTRUMENTS_LAST_REFRESH_TS)) == b""

    # JSON-typed view keys
    pnl = orjson.loads(await redis.get(K.UI_VIEW_PNL))
    assert pnl == {"realized": 0, "unrealized": 0, "day": 0}


async def test_per_index_runtime_keys(redis) -> None:
    await redis_template.apply(redis, flush_runtime=False)
    for idx in K.INDEXES:
        assert (await redis.get(K.strategy_state(idx))) == b"FLAT"
        assert (await redis.get(K.strategy_enabled(idx))) == b"true"
        assert (await redis.get(K.strategy_counters_entries_today(idx))) == b"0"
        assert (await redis.get(K.delta_pcr_cumulative(idx))) == b"1.0"
        assert (await redis.get(K.delta_pcr_mode(idx))) == b"1"


async def test_flush_preserves_user_and_strategy_configs(redis) -> None:
    # Pre-populate preserved namespaces
    await redis.set("user:account:username", "alice")
    await redis.set("strategy:configs:execution", "{}")
    # Pre-populate runtime keys that should be cleared
    await redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "true")
    await redis.set("strategy:nifty50:state", "IN_CE")  # runtime, not configs:*
    await redis.set("orders:positions:open", "stale")

    deleted = await redis_template.flush_runtime_namespaces(redis)
    assert deleted >= 3

    # Preserved still present
    assert (await redis.get("user:account:username")) == b"alice"
    assert (await redis.get("strategy:configs:execution")) == b"{}"
    # Runtime cleared
    assert (await redis.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)) is None
    assert (await redis.get("strategy:nifty50:state")) is None
    assert (await redis.get("orders:positions:open")) is None


async def test_apply_with_flush_resets_runtime(redis) -> None:
    await redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "true")
    await redis.set(K.strategy_state("nifty50"), "IN_PE")
    out = await redis_template.apply(redis, flush_runtime=True)
    assert out["deleted"] >= 1
    # After flush + apply, runtime is at canonical defaults
    assert (await redis.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)) == b"false"
    assert (await redis.get(K.strategy_state("nifty50"))) == b"FLAT"
