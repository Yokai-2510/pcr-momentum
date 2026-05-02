"""
engines.background.log_rotation — placeholder.

In production, log rotation is owned by `logrotate.d/trading` (Phase 11).
This module exists so the Background engine can record an "I ran"
heartbeat each nightly cycle for observability dashboards.
"""

from __future__ import annotations

import time

import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K


async def record_run(redis_async: _redis_async.Redis) -> None:
    """Update `system:health:engines` HASH with the rotation timestamp."""
    log = logger.bind(engine="background", task="log_rotation")
    try:
        await redis_async.hset(  # type: ignore[misc]
            K.SYSTEM_HEALTH_ENGINES,
            "last_log_rotation_ts_ms",
            str(int(time.time() * 1000)),
        )
        log.info("log_rotation: heartbeat recorded")
    except Exception as e:
        log.warning(f"log_rotation: hset failed: {e!r}")
