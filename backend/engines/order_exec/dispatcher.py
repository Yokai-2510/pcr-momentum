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


def _opt_float(payload: dict[str, str], key: str) -> float | None:
    raw = payload.get(key)
    if not raw or raw.lower() in {"none", "null", ""}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _opt_int(payload: dict[str, str], key: str) -> int | None:
    raw = payload.get(key)
    if not raw or raw.lower() in {"none", "null", ""}:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _opt_dict(payload: dict[str, str], key: str) -> dict:
    raw = payload.get(key)
    if not raw:
        return {}
    try:
        import orjson
        parsed = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _signal_from_payload(payload: dict[str, str]) -> Signal | None:
    """Reconstruct a Signal from stream entry fields (all string-typed in Redis).

    Signal payload v2 (Strategy.md §9.4) — strategy_id + score are first-class.
    `instrument_id` and `index` are duplicated for backward-compat.
    """
    try:
        instrument_id = payload.get("instrument_id") or payload.get("index", "nifty50")
        return Signal.model_validate({
            "sig_id": payload["sig_id"],
            "strategy_id": payload.get("strategy_id", "bid_ask_imbalance_v1"),
            "instrument_id": instrument_id,
            "index": payload.get("index") or instrument_id,
            "side": payload["side"],
            "strike": int(payload["strike"]),
            "instrument_token": payload["instrument_token"],
            "intent": payload["intent"],
            "qty_lots": int(payload["qty_lots"]),
            "score": _opt_float(payload, "score"),
            "score_breakdown": _opt_dict(payload, "score_breakdown"),
            "net_pressure_at_signal": _opt_float(payload, "net_pressure_at_signal"),
            "decision_ts": _opt_int(payload, "decision_ts") or int(__import__("time").time() * 1000),
            "diff_at_signal": _opt_float(payload, "diff_at_signal") or 0.0,
            "sum_ce_at_signal": _opt_float(payload, "sum_ce_at_signal") or 0.0,
            "sum_pe_at_signal": _opt_float(payload, "sum_pe_at_signal") or 0.0,
            "delta_at_signal": _opt_float(payload, "delta_at_signal") or 0.0,
            "delta_pcr_at_signal": _opt_float(payload, "delta_pcr_at_signal"),
            "strategy_version": payload.get("strategy_version", ""),
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
