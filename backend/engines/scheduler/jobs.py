"""
engines.scheduler.jobs — pure cron-job functions.

Each job is a coroutine that:
  1. (optionally) flips a Redis flag the engines read directly
  2. publishes one entry to `system:stream:scheduler_events` with the
     `kind` field set so consumers (Background, Strategy, Order Exec)
     can dispatch on it.

Keeping the side-effects here (out of the APScheduler runner) makes them
unit-testable without spinning up the scheduler itself.
"""

from __future__ import annotations

import time

import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K


async def _publish(
    redis_async: _redis_async.Redis, kind: str, **fields: str
) -> str:
    """XADD a scheduler event. Returns the entry id (or empty on failure)."""
    payload = {"kind": kind, "ts_ms": str(int(time.time() * 1000)), **fields}
    try:
        entry_id = await redis_async.xadd(  # type: ignore[misc]
            K.SYSTEM_STREAM_SCHEDULER_EVENTS,
            payload,
            maxlen=10_000,
            approximate=True,
        )
    except Exception as e:
        logger.bind(engine="scheduler").warning(
            f"_publish({kind}) failed: {e!r}"
        )
        return ""
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode()
    return str(entry_id)


async def pre_open_snapshot(redis_async: _redis_async.Redis) -> None:
    """Strategy reads this; flag flips on so it can take its pre-open snapshot."""
    await redis_async.set(K.SYSTEM_FLAGS_DATA_PIPELINE_SUBSCRIBED, "true")  # type: ignore[misc]
    await _publish(redis_async, "pre_open_snapshot")


async def market_open(redis_async: _redis_async.Redis) -> None:
    """Mark trading active. Init must have run successfully for this to take."""
    await redis_async.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")  # type: ignore[misc]
    await redis_async.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "none")  # type: ignore[misc]
    await _publish(redis_async, "market_open")


async def entry_freeze(redis_async: _redis_async.Redis) -> None:
    """Strategy refuses new entries after this; existing positions still managed."""
    await redis_async.set("system:flags:entry_freeze", "true")  # type: ignore[misc]
    await _publish(redis_async, "entry_freeze")


async def eod_squareoff(redis_async: _redis_async.Redis) -> None:
    """Order Exec exit_eval already triggers EOD via clock; this is the audit log."""
    await redis_async.set("system:flags:eod_squareoff", "true")  # type: ignore[misc]
    await _publish(redis_async, "eod_squareoff")


async def market_close(redis_async: _redis_async.Redis) -> None:
    await redis_async.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")  # type: ignore[misc]
    await redis_async.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "market_closed")  # type: ignore[misc]
    await _publish(redis_async, "market_close")


async def daily_reset(redis_async: _redis_async.Redis) -> None:
    """Background consumes this and clears closed_today / DLC / pnl_day."""
    await redis_async.delete("system:flags:entry_freeze")  # type: ignore[misc]
    await redis_async.delete("system:flags:eod_squareoff")  # type: ignore[misc]
    await _publish(redis_async, "daily_reset")


async def instrument_refresh(redis_async: _redis_async.Redis) -> None:
    await _publish(redis_async, "instrument_refresh")


async def nightly_maintenance(redis_async: _redis_async.Redis) -> None:
    await _publish(redis_async, "nightly_maintenance")
