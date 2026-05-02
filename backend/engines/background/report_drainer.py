"""
engines.background.report_drainer — bridge worker → Postgres.

Background owns its own asyncpg pool (one event loop, one connection
acquire path) so the cross-loop hang from Phase 7 Bug 1 cannot recur.

Loop: BLPOP `orders:reports:pending` → reconstruct ClosedPositionReport →
INSERT via `engines.order_exec.reporting.persist_report`. On INSERT
failure the payload is re-pushed to the head of the list with a small
backoff so transient PG outages don't drop reports.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import orjson
import redis.asyncio as _redis_async
from loguru import logger

from engines.order_exec import reporting
from state import keys as K
from state.schemas.report import ClosedPositionReport

_DRAIN_BLOCK_SEC = 5
_RETRY_BACKOFF_SEC = 10


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


async def _persist_one(
    pool: asyncpg.Pool, payload_str: str
) -> tuple[bool, str]:
    """Validate + INSERT a single buffered report. Returns (ok, reason)."""
    try:
        payload = orjson.loads(payload_str)
    except Exception as e:
        return False, f"json_decode_failed:{e!r}"
    try:
        report = ClosedPositionReport.model_validate(payload)
    except Exception as e:
        # Malformed payload — drop on the floor; logging gives the audit
        # trail. Re-pushing would loop forever.
        return False, f"schema_validation_failed:{e!r}"
    try:
        await reporting.persist_report(pool, report)
    except Exception as e:
        return False, f"db_insert_failed:{e!r}"
    return True, "ok"


async def drain_loop(
    redis_async: _redis_async.Redis,
    pool: asyncpg.Pool,
    *,
    shutdown: asyncio.Event | None = None,
) -> None:
    """Single async drain loop. One BLPOP, one INSERT, repeat."""
    log = logger.bind(engine="background", task="report_drainer")
    log.info("report_drainer: started")
    drained = 0
    requeued = 0

    while shutdown is None or not shutdown.is_set():
        try:
            popped = await redis_async.blpop(  # type: ignore[misc]
                K.ORDERS_REPORTS_PENDING, timeout=_DRAIN_BLOCK_SEC
            )
        except Exception as e:
            log.warning(f"BLPOP failed: {e!r}")
            await asyncio.sleep(1.0)
            continue
        if not popped:
            continue
        # popped is (key, value)
        _key, raw = popped
        payload_str = _decode(raw)
        ok, reason = await _persist_one(pool, payload_str)
        if ok:
            drained += 1
            if drained % 50 == 0:
                log.info(f"drained {drained} reports so far")
            continue
        if reason.startswith("schema_validation_failed") or reason.startswith(
            "json_decode_failed"
        ):
            log.error(f"report_drainer: dropping malformed payload: {reason}")
            continue
        # DB / transient failure → re-queue at the head and backoff.
        try:
            await redis_async.lpush(K.ORDERS_REPORTS_PENDING, payload_str)
            requeued += 1
            log.warning(
                f"report_drainer: requeued report ({reason}); backing off "
                f"{_RETRY_BACKOFF_SEC}s; total_requeued={requeued}"
            )
        except Exception as e:
            log.exception(f"requeue failed: {e!r}")
        await asyncio.sleep(_RETRY_BACKOFF_SEC)

    log.info(f"report_drainer: stopping; drained={drained} requeued={requeued}")
