"""
Signal publisher — write Action -> Signal -> XADD strategy:stream:signals.

Decouples the runner (which builds Actions) from Redis emission (which
involves payload schema, sig_id minting, and dedup).

Signal schema v3 (Strategy.md §9.4, Step 3 trim) carries:
    sig_id, strategy_id, instrument_id, intent, side, strike, instrument_token,
    qty_lots, score, score_breakdown, net_pressure_at_signal, decision_ts,
    metrics_at_signal (free-form numeric blob — the strategy's Action.metrics)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from engines.strategy.strategies.base import Action, ActionKind
from state import keys as K
from state.schemas.signal import Signal, SignalIntent

_KIND_TO_INTENT: dict[ActionKind, SignalIntent] = {
    ActionKind.ENTER: SignalIntent.FRESH_ENTRY,
    ActionKind.FLIP: SignalIntent.REVERSAL_FLIP,
    ActionKind.EXIT: SignalIntent.MANUAL_EXIT,  # exit signals get this intent for now
}


def _json_sanitize(blob: dict[str, Any]) -> dict[str, Any]:
    """Best-effort JSON round-trip: drops keys whose values can't serialize."""
    out: dict[str, Any] = {}
    for k, v in blob.items():
        try:
            orjson.dumps(v)
        except Exception:
            try:
                v = orjson.loads(orjson.dumps(v, default=str))
            except Exception:
                continue
        out[str(k)] = v
    return out


def _mint_sig_id(strategy_id: str, instrument_id: str, action: Action, ts_ms: int) -> str:
    raw = f"{strategy_id}|{instrument_id}|{action.kind.value}|{action.side}|{action.strike}|{ts_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def emit_signal(
    redis_async: _redis_async.Redis,
    *,
    strategy_id: str,
    instrument_id: str,
    action: Action,
) -> str | None:
    """Persist + XADD a Signal for an actionable Action.

    Returns the sig_id, or None if the Action kind is non-emitting (NO_OP/HOLD/REVERSAL_WARN).
    """
    log = logger.bind(engine="strategy", component="publisher", sid=strategy_id, idx=instrument_id)

    if action.kind not in _KIND_TO_INTENT:
        return None  # NO_OP, HOLD, REVERSAL_WARN are not actionable

    intent = _KIND_TO_INTENT[action.kind]

    if not action.side or not action.strike or not action.instrument_token:
        log.warning(f"emit_signal: incomplete action {action!r}")
        return None

    ts = datetime.now(UTC)
    ts_ms = int(ts.timestamp() * 1000)
    sig_id = _mint_sig_id(strategy_id, instrument_id, action, ts_ms)

    metrics = action.metrics or {}
    sig = Signal(
        sig_id=sig_id,
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        index=instrument_id,  # legacy compat for current order_exec readers
        side=action.side,  # type: ignore[arg-type]
        strike=int(action.strike),
        instrument_token=action.instrument_token,
        intent=intent,
        qty_lots=int(action.qty_lots or 1),
        score=action.score,
        score_breakdown=action.score_breakdown or {},
        net_pressure_at_signal=metrics.get("net_pressure"),
        decision_ts=ts_ms,
        ts=ts,
        # Free-form forensic metrics — whatever numeric values the strategy
        # put on its Action. Strategy-defined names; no central schema.
        metrics_at_signal={k: float(v) for k, v in metrics.items() if isinstance(v, int | float)},
        # FULL decision-state snapshot: Action.snapshot when provided, else
        # the whole metrics dict (nested values included) — JSON-sanitized.
        strategy_snapshot=_json_sanitize(
            action.snapshot if action.snapshot is not None else metrics
        ),
    )
    payload = sig.model_dump(mode="json")
    payload_json = orjson.dumps(payload)
    sig_key = K.strategy_signal(sig_id)

    created = bool(await redis_async.set(sig_key, payload_json, nx=True))
    if not created:
        # Same hash already published this tick — silently dedupe.
        return sig_id

    stream_fields: dict[str, str | int | float] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str | int | float):
            stream_fields[key] = value
        else:
            stream_fields[key] = orjson.dumps(value).decode()

    await redis_async.xadd(
        K.STRATEGY_STREAM_SIGNALS,
        cast(Any, stream_fields),
        maxlen=5_000,
        approximate=True,
    )

    log.info(
        f"emit {intent.value} {action.side} strike={action.strike} qty={action.qty_lots} "
        f"score={action.score} sig={sig_id}"
    )
    return sig_id
