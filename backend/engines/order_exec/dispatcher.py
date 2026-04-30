"""
engines.order_exec.dispatcher — async tail of `strategy:stream:signals`.

Runs a single async loop that XREADGROUPs the strategy signal stream, parses
each entry into a `Signal`, and pushes onto the worker work queue. The work
queue is a thread-safe `queue.Queue` consumed by N worker threads.

ACK semantics: we ACK the stream entry as soon as it's parsed + queued. The
worker is fully responsible for the rest (rejected_signals, reporting,
cleanup). If a worker crashes the entry is gone — but we still have the
Signal dict in `strategy:signals:{sig_id}` for forensic recovery.
"""

from __future__ import annotations

import asyncio
import queue
from typing import Any

import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K
from state.schemas.signal import Signal

CONSUMER_GROUP = "order_exec"
DISPATCHER_BLOCK_MS = 1000


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


async def _ensure_consumer_group(redis_async: _redis_async.Redis) -> None:
    try:
        await redis_async.xgroup_create(  # type: ignore[misc]
            K.STRATEGY_STREAM_SIGNALS, CONSUMER_GROUP, id="$", mkstream=True
        )
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.warning(f"xgroup_create raised: {e!r}")


async def _signal_from_payload(payload: dict[str, str]) -> Signal | None:
    """Reconstruct a Signal from stream entry fields (all string-typed in Redis)."""
    try:
        return Signal.model_validate({
            "sig_id": payload["sig_id"],
            "index": payload["index"],
            "side": payload["side"],
            "strike": int(payload["strike"]),
            "instrument_token": payload["instrument_token"],
            "intent": payload["intent"],
            "qty_lots": int(payload["qty_lots"]),
            "diff_at_signal": float(payload.get("diff_at_signal", 0.0)),
            "sum_ce_at_signal": float(payload.get("sum_ce_at_signal", 0.0)),
            "sum_pe_at_signal": float(payload.get("sum_pe_at_signal", 0.0)),
            "delta_at_signal": float(payload.get("delta_at_signal", 0.0)),
            "delta_pcr_at_signal": (
                float(payload["delta_pcr_at_signal"])
                if payload.get("delta_pcr_at_signal")
                and payload["delta_pcr_at_signal"].lower() not in {"none", "null", ""}
                else None
            ),
            "strategy_version": payload.get("strategy_version", "unknown"),
            "ts": payload["ts"],
        })
    except Exception as e:
        logger.warning(f"signal_from_payload failed: {e!r} payload={payload!r}")
        return None


async def dispatcher_loop(
    redis_async: _redis_async.Redis,
    work_queue: queue.Queue,
    *,
    consumer_name: str = "dispatcher",
    shutdown: asyncio.Event | None = None,
) -> None:
    """Tail the signal stream and queue each parsed Signal to a worker."""
    log = logger.bind(engine="order_exec", loop="dispatcher")
    await _ensure_consumer_group(redis_async)
    log.info("dispatcher: started")

    while shutdown is None or not shutdown.is_set():
        try:
            resp = await redis_async.xreadgroup(  # type: ignore[misc]
                CONSUMER_GROUP,
                consumer_name,
                {K.STRATEGY_STREAM_SIGNALS: ">"},
                count=10,
                block=DISPATCHER_BLOCK_MS,
            )
        except Exception as e:
            log.warning(f"dispatcher xreadgroup failed: {e!r}")
            await asyncio.sleep(0.5)
            continue
        if not resp:
            continue

        for _stream, entries in resp:
            for entry_id, fields in entries:
                payload = {_decode(k): _decode(v) for k, v in fields.items()}
                sig = await _signal_from_payload(payload)
                if sig is None:
                    log.warning(f"unparseable signal entry {entry_id!r}; ACKing and dropping")
                    await redis_async.xack(K.STRATEGY_STREAM_SIGNALS, CONSUMER_GROUP, entry_id)  # type: ignore[misc]
                    continue
                work_queue.put(sig)
                await redis_async.xack(K.STRATEGY_STREAM_SIGNALS, CONSUMER_GROUP, entry_id)  # type: ignore[misc]
                log.info(f"dispatched sig={sig.sig_id} index={sig.index} side={sig.side}")

    log.info("dispatcher: shutdown signaled; exiting")
