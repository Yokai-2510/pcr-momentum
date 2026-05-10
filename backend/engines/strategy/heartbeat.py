"""
Per-vessel heartbeat (Strategy.md §11.4).

Each vessel writes its heartbeat every 5 s. The health engine flags any
vessel whose heartbeat is older than 30 s during LIVE phase as RED. This
makes silent-loop bugs (the NIFTY failure observed on 2026-05-07)
architecturally impossible — a vessel that isn't ticking is detected within
30 seconds and surfaced on /api/health.
"""

from __future__ import annotations

import asyncio
import time

import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K

HEARTBEAT_INTERVAL_SEC = 5


async def heartbeat_task(
    redis_async: _redis_async.Redis,
    *,
    engine_name: str,                     # "strategy" or "strategy:{sid}" when isolated
    vessel_keys: list[tuple[str, str]],   # [(strategy_id, instrument_id), ...]
    shutdown: asyncio.Event,
) -> None:
    """Periodically HSET heartbeat fields for every active vessel + the engine itself."""
    log = logger.bind(engine="strategy", component="heartbeat")
    log.info(f"heartbeat: started for engine={engine_name} vessels={len(vessel_keys)}")
    while not shutdown.is_set():
        ts_ms = str(int(time.time() * 1000))
        try:
            mapping: dict[str, str] = {engine_name: ts_ms}  # the engine itself
            for sid, idx in vessel_keys:
                mapping[K.heartbeat_field_vessel(sid, idx)] = ts_ms
            await redis_async.hset(K.SYSTEM_HEALTH_HEARTBEATS, mapping=mapping)  # type: ignore[misc]
        except Exception as exc:
            log.warning(f"heartbeat HSET failed: {exc!r}")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=HEARTBEAT_INTERVAL_SEC)
        except (TimeoutError, asyncio.TimeoutError):
            continue
    log.info("heartbeat: shutdown")
