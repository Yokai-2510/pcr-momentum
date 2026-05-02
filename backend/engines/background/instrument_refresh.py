"""
engines.background.instrument_refresh — re-pull NSE master into Redis HASH.

Daily refresh keeps `market_data:instruments:master` aligned with Upstox's
overnight master rotation. Called from the Scheduler's instrument_refresh
event (05:30 IST) AND on demand via `/commands/instrument_refresh`
(Phase 9).

Reuses `engines.init.instruments_loader.load_master_instruments`, which
already wipes-and-rewrites idempotently.
"""

from __future__ import annotations

import redis.asyncio as _redis_async
from loguru import logger

from engines.init import instruments_loader


async def refresh(
    redis_async: _redis_async.Redis,
) -> int:
    """Run the loader. Returns number of instruments written."""
    log = logger.bind(engine="background", task="instrument_refresh")
    log.info("instrument_refresh: starting")
    try:
        n = await instruments_loader.load_master_instruments(redis_async)
    except Exception as e:
        log.exception(f"instrument_refresh failed: {e!r}")
        return 0
    log.info(f"instrument_refresh: completed; {n} rows")
    return n
