"""Tests for engines.scheduler.jobs — each cron-job side-effects.

We don't run APScheduler itself; we invoke each job coroutine directly
against fakeredis_async and assert on the resulting Redis state.
"""

from __future__ import annotations

from typing import Any

import pytest

from engines.scheduler import jobs
from state import keys as K


@pytest.mark.asyncio
async def test_market_open_flips_trading_active(fake_redis_async: Any) -> None:
    await jobs.market_open(fake_redis_async)
    assert (await fake_redis_async.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)) == "true"
    assert (await fake_redis_async.get(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON)) == "none"
    # Event published.
    entries = await fake_redis_async.xrevrange(
        K.SYSTEM_STREAM_SCHEDULER_EVENTS, count=1
    )
    assert entries
    fields = entries[0][1]
    assert fields["kind"] == "market_open"


@pytest.mark.asyncio
async def test_market_close_flips_trading_active_off(fake_redis_async: Any) -> None:
    await fake_redis_async.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    await jobs.market_close(fake_redis_async)
    assert (await fake_redis_async.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)) == "false"
    entries = await fake_redis_async.xrevrange(
        K.SYSTEM_STREAM_SCHEDULER_EVENTS, count=1
    )
    assert entries[0][1]["kind"] == "market_close"


@pytest.mark.asyncio
async def test_entry_freeze_sets_flag(fake_redis_async: Any) -> None:
    await jobs.entry_freeze(fake_redis_async)
    assert (await fake_redis_async.get("system:flags:entry_freeze")) == "true"


@pytest.mark.asyncio
async def test_daily_reset_clears_freeze_and_publishes(fake_redis_async: Any) -> None:
    await fake_redis_async.set("system:flags:entry_freeze", "true")
    await fake_redis_async.set("system:flags:eod_squareoff", "true")
    await jobs.daily_reset(fake_redis_async)
    assert (await fake_redis_async.get("system:flags:entry_freeze")) is None
    assert (await fake_redis_async.get("system:flags:eod_squareoff")) is None
    entries = await fake_redis_async.xrevrange(
        K.SYSTEM_STREAM_SCHEDULER_EVENTS, count=1
    )
    assert entries[0][1]["kind"] == "daily_reset"


@pytest.mark.asyncio
async def test_instrument_refresh_publishes_event(fake_redis_async: Any) -> None:
    await jobs.instrument_refresh(fake_redis_async)
    entries = await fake_redis_async.xrevrange(
        K.SYSTEM_STREAM_SCHEDULER_EVENTS, count=1
    )
    assert entries
    assert entries[0][1]["kind"] == "instrument_refresh"


@pytest.mark.asyncio
async def test_nightly_maintenance_publishes_event(fake_redis_async: Any) -> None:
    await jobs.nightly_maintenance(fake_redis_async)
    entries = await fake_redis_async.xrevrange(
        K.SYSTEM_STREAM_SCHEDULER_EVENTS, count=1
    )
    assert entries[0][1]["kind"] == "nightly_maintenance"
