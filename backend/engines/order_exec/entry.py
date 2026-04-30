"""
engines.order_exec.entry — Stage B + C.

Entry submission + monitor. Two modes:

  paper  — simulate fill at `best_ask + buffer_inr` against the live chain
           leaf; no broker call. Returns immediately.
  live   — UpstoxAPI.place_order(DAY LIMIT slice=true), then loop on portfolio
           WS events from `orders:stream:order_events` watching for FILLED
           / PARTIAL / OPEN / REJECTED. Drift handling per Strategy.md §10.3.

Defaults (configurable via strategy:configs:execution):
  buffer_inr           = ₹2
  drift_threshold_inr  = ₹3
  chase_ceiling_inr    = ₹15
  open_timeout_sec     = 8
  partial_grace_sec    = 3
  max_retries          = 2
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson
import redis as _redis_sync
from loguru import logger

from brokers.upstox import UpstoxAPI
from state import keys as K
from state.schemas.signal import Signal


@dataclass
class EntryResult:
    filled_qty: int
    avg_fill_price: float
    order_id: str
    order_events: list[dict[str, Any]] = field(default_factory=list)
    abandon_reason: str | None = None
    submit_to_ack_ms: int = 0
    ack_to_fill_ms: int = 0


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _read_execution_config(redis_sync: _redis_sync.Redis) -> dict[str, Any]:
    raw = redis_sync.get(K.STRATEGY_CONFIGS_EXECUTION)
    if not raw:
        return {}
    blob = raw if isinstance(raw, bytes) else raw.encode()
    parsed = orjson.loads(blob)
    return parsed if isinstance(parsed, dict) else {}


def _read_leaf(
    redis_sync: _redis_sync.Redis, index: str, token: str
) -> dict[str, Any] | None:
    raw = redis_sync.get(K.market_data_index_option_chain(index))
    if not raw:
        return None
    blob = raw if isinstance(raw, bytes) else raw.encode()
    chain = orjson.loads(blob)
    if not isinstance(chain, dict):
        return None
    for _strike, sides in chain.items():
        if not isinstance(sides, dict):
            continue
        for side in ("ce", "pe"):
            leaf = sides.get(side)
            if isinstance(leaf, dict) and leaf.get("token") == token:
                return leaf
    return None


def _now_ts_ms() -> int:
    return int(time.time() * 1000)


def submit_and_monitor_paper(
    redis_sync: _redis_sync.Redis,
    signal: Signal,
    pos_id: str,
    lot_size: int,
) -> EntryResult:
    """Paper-mode entry. Reads current ask, simulates immediate fill at ask+buffer."""
    log = logger.bind(engine="order_exec", index=signal.index, pos_id=pos_id)
    cfg = _read_execution_config(redis_sync)
    buffer_inr = float(cfg.get("buffer_inr") or 2.0)

    submit_ts = _now_ts_ms()
    leaf = _read_leaf(redis_sync, signal.index, signal.instrument_token)
    if leaf is None:
        return EntryResult(
            filled_qty=0,
            avg_fill_price=0.0,
            order_id="",
            abandon_reason="leaf_missing",
        )
    ask = float(leaf.get("ask") or 0)
    if ask <= 0:
        return EntryResult(
            filled_qty=0,
            avg_fill_price=0.0,
            order_id="",
            abandon_reason="no_ask",
        )

    fill_price = round(ask + buffer_inr, 2)
    qty = signal.qty_lots * lot_size
    order_id = f"PAPER-{uuid.uuid4().hex[:12]}"
    fill_ts = _now_ts_ms()

    events = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "SUBMIT",
            "order_id": order_id,
            "qty": qty,
            "price": fill_price,
            "broker_status": "paper_submit",
        },
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "FILL",
            "order_id": order_id,
            "qty": qty,
            "price": fill_price,
            "broker_status": "paper_filled",
        },
    ]

    log.info(
        f"entry[paper]: filled {qty} @ ₹{fill_price} (ask={ask:.2f} buffer={buffer_inr})"
    )
    return EntryResult(
        filled_qty=qty,
        avg_fill_price=fill_price,
        order_id=order_id,
        order_events=events,
        submit_to_ack_ms=0,
        ack_to_fill_ms=fill_ts - submit_ts,
    )


def submit_and_monitor_live(
    redis_sync: _redis_sync.Redis,
    signal: Signal,
    pos_id: str,
    access_token: str,
    lot_size: int,
) -> EntryResult:
    """Live-mode entry. Places DAY LIMIT, monitors via portfolio WS events."""
    log = logger.bind(engine="order_exec", index=signal.index, pos_id=pos_id)
    cfg = _read_execution_config(redis_sync)
    buffer_inr = float(cfg.get("buffer_inr") or 2.0)
    drift_threshold = float(cfg.get("drift_threshold_inr") or 3.0)
    chase_ceiling = float(cfg.get("chase_ceiling_inr") or 15.0)
    open_timeout_sec = int(cfg.get("open_timeout_sec") or 8)
    partial_grace_sec = int(cfg.get("partial_grace_sec") or 3)

    leaf = _read_leaf(redis_sync, signal.index, signal.instrument_token)
    if leaf is None:
        return EntryResult(0, 0.0, "", abandon_reason="leaf_missing")
    initial_ask = float(leaf.get("ask") or 0)
    if initial_ask <= 0:
        return EntryResult(0, 0.0, "", abandon_reason="no_ask")

    submit_price = round(initial_ask + buffer_inr, 2)
    qty = signal.qty_lots * lot_size

    submit_ts = _now_ts_ms()
    res = UpstoxAPI.place_order({
        "instrument_token": signal.instrument_token,
        "quantity": qty,
        "transaction_type": "BUY",
        "access_token": access_token,
        "price": submit_price,
        "order_type": "LIMIT",
        "product": "I",
        "validity": "DAY",
        "tag": pos_id,
        "slice": True,
    })
    if not res["success"]:
        log.warning(f"entry[live]: place_order rejected: {res['error']}")
        return EntryResult(0, 0.0, "", abandon_reason=f"place_rejected:{res['error']}")

    order_id = res["data"].get("first_order_id") or ""
    ack_ts = _now_ts_ms()
    log.info(f"entry[live]: order_id={order_id} submit @ ₹{submit_price}")

    events: list[dict[str, Any]] = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "SUBMIT",
            "order_id": order_id,
            "qty": qty,
            "price": submit_price,
        },
    ]

    # Monitor via the broker portfolio-WS event stream Background publishes.
    deadline_ts = time.time() + open_timeout_sec
    last_partial_seen_ts: float | None = None
    fill_ts: int | None = None
    filled_qty = 0
    avg_fill_price = 0.0
    last_id = "$"
    while time.time() < deadline_ts:
        try:
            resp = redis_sync.xread(
                {K.ORDERS_STREAM_ORDER_EVENTS: last_id},
                count=10,
                block=500,
            )
        except Exception as e:
            log.warning(f"xread orders:stream:order_events failed: {e!r}")
            break
        if not resp:
            # Drift check: if ask has moved beyond chase_ceiling, abandon.
            cur_leaf = _read_leaf(redis_sync, signal.index, signal.instrument_token)
            if cur_leaf is not None:
                cur_ask = float(cur_leaf.get("ask") or 0)
                if cur_ask > 0 and cur_ask - initial_ask >= chase_ceiling:
                    UpstoxAPI.cancel_order({"order_id": order_id, "access_token": access_token})
                    return EntryResult(
                        0, 0.0, order_id, events,
                        abandon_reason=f"chase_ceiling_breached:{cur_ask:.2f}",
                    )
                if cur_ask > 0 and cur_ask - initial_ask >= drift_threshold:
                    new_price = round(cur_ask + buffer_inr, 2)
                    UpstoxAPI.modify_order({
                        "order_id": order_id,
                        "access_token": access_token,
                        "price": new_price,
                    })
                    initial_ask = cur_ask
                    events.append({
                        "ts": datetime.now(UTC).isoformat(),
                        "event_type": "MODIFY",
                        "order_id": order_id,
                        "price": new_price,
                    })
            continue
        for _stream, entries in resp:
            for entry_id, fields in entries:
                last_id = _decode(entry_id)
                payload = {_decode(k): _decode(v) for k, v in fields.items()}
                if payload.get("order_id") != order_id:
                    continue
                events.append(payload)
                event_type = payload.get("event_type", "")
                if event_type == "FILL":
                    filled_qty = int(payload.get("filled_qty") or qty)
                    avg_fill_price = float(payload.get("avg_price") or submit_price)
                    fill_ts = _now_ts_ms()
                elif event_type == "PARTIAL_FILL":
                    last_partial_seen_ts = time.time()
                elif event_type == "REJECT":
                    return EntryResult(
                        0, 0.0, order_id, events,
                        abandon_reason=f"broker_reject:{payload.get('reject_reason')}",
                    )
        if fill_ts is not None:
            break
        # Cancel partial-fill remainder after grace
        if last_partial_seen_ts and time.time() - last_partial_seen_ts > partial_grace_sec:
            UpstoxAPI.cancel_order({"order_id": order_id, "access_token": access_token})
            break

    if filled_qty == 0:
        UpstoxAPI.cancel_order({"order_id": order_id, "access_token": access_token})
        return EntryResult(
            0, 0.0, order_id, events, abandon_reason="open_timeout",
        )

    return EntryResult(
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        order_id=order_id,
        order_events=events,
        submit_to_ack_ms=ack_ts - submit_ts,
        ack_to_fill_ms=(fill_ts or _now_ts_ms()) - ack_ts,
    )


def submit_and_monitor(
    redis_sync: _redis_sync.Redis,
    signal: Signal,
    pos_id: str,
    *,
    mode: str,
    access_token: str,
    lot_size: int,
) -> EntryResult:
    """Mode-routed entry."""
    if mode == "paper":
        return submit_and_monitor_paper(redis_sync, signal, pos_id, lot_size)
    return submit_and_monitor_live(redis_sync, signal, pos_id, access_token, lot_size)
