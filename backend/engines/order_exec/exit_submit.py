"""
engines.order_exec.exit_submit — Stage E.

Modify-only SELL DAY LIMIT loop. Exits **never abandon** — the position
must close. Strategy.md §10.4:

  1. Place SELL DAY LIMIT at best_bid minus buffer_inr.
  2. Wait for fill via portfolio WS.
  3. If OPEN and bid drifted → modify (do not cancel).
  4. If REJECTED → refresh quote and retry place.
  5. Continue until FILLED.

After 15:00 IST the buffer widens to `eod_buffer_inr` to fill against thin
EOD books faster.
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
from state.schemas.position import Position


@dataclass
class ExitResult:
    filled_qty: int
    avg_fill_price: float
    order_id: str
    order_events: list[dict[str, Any]] = field(default_factory=list)
    decision_to_exit_submit_ms: int = 0
    exit_submit_to_fill_ms: int = 0


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


def _effective_buffer(now_hhmm: str, cfg: dict[str, Any]) -> float:
    buffer_inr = float(cfg.get("buffer_inr") or 2.0)
    eod_buffer = float(cfg.get("eod_buffer_inr") or 5.0)
    suppress_after = str(cfg.get("liquidity_exit_suppress_after") or "15:00")
    return eod_buffer if now_hhmm >= suppress_after else buffer_inr


def submit_and_complete_paper(
    redis_sync: _redis_sync.Redis,
    position: Position,
    exit_reason: str,
    now_hhmm: str,
) -> ExitResult:
    """Paper-mode exit — fills immediately at best_bid minus buffer."""
    log = logger.bind(engine="order_exec", index=position.index, pos_id=position.pos_id)
    cfg = _read_execution_config(redis_sync)
    buffer_inr = _effective_buffer(now_hhmm, cfg)

    submit_ts = _now_ts_ms()
    leaf = _read_leaf(redis_sync, position.index, position.instrument_token)
    bid = float(leaf.get("bid") or 0) if leaf else 0.0
    if bid <= 0:
        # Fallback: use current_premium so the close still completes.
        bid = float(position.current_premium or position.entry_price)

    fill_price = round(max(0.05, bid - buffer_inr), 2)
    order_id = f"PAPER-X-{uuid.uuid4().hex[:12]}"
    fill_ts = _now_ts_ms()

    events = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "SUBMIT",
            "order_id": order_id,
            "qty": int(position.qty),
            "price": fill_price,
            "broker_status": "paper_submit",
            "note": f"exit:{exit_reason}",
        },
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "FILL",
            "order_id": order_id,
            "qty": int(position.qty),
            "price": fill_price,
            "broker_status": "paper_filled",
        },
    ]
    log.info(
        f"exit[paper:{exit_reason}]: filled {position.qty} @ ₹{fill_price} (bid={bid:.2f})"
    )
    return ExitResult(
        filled_qty=int(position.qty),
        avg_fill_price=fill_price,
        order_id=order_id,
        order_events=events,
        decision_to_exit_submit_ms=0,
        exit_submit_to_fill_ms=fill_ts - submit_ts,
    )


def submit_and_complete_live(
    redis_sync: _redis_sync.Redis,
    position: Position,
    exit_reason: str,
    access_token: str,
    now_hhmm: str,
) -> ExitResult:
    """Live-mode exit — modify-only SELL loop, never abandons."""
    log = logger.bind(engine="order_exec", index=position.index, pos_id=position.pos_id)
    cfg = _read_execution_config(redis_sync)
    buffer_inr = _effective_buffer(now_hhmm, cfg)

    leaf = _read_leaf(redis_sync, position.index, position.instrument_token)
    bid = float(leaf.get("bid") or 0) if leaf else 0.0
    if bid <= 0:
        bid = float(position.current_premium or position.entry_price)

    submit_ts = _now_ts_ms()
    submit_price = round(max(0.05, bid - buffer_inr), 2)
    res = UpstoxAPI.place_order({
        "instrument_token": position.instrument_token,
        "quantity": int(position.qty),
        "transaction_type": "SELL",
        "access_token": access_token,
        "price": submit_price,
        "order_type": "LIMIT",
        "product": "I",
        "validity": "DAY",
        "tag": position.pos_id,
        "slice": True,
    })
    if not res["success"]:
        # Cannot abandon — refresh and retry place.
        log.warning(f"exit[live]: place rejected, retrying: {res['error']}")

    order_id = ((res.get("data") or {}).get("first_order_id") or "") if res["success"] else ""
    events: list[dict[str, Any]] = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": "SUBMIT",
            "order_id": order_id,
            "qty": int(position.qty),
            "price": submit_price,
            "note": f"exit:{exit_reason}",
        },
    ]

    last_id = "$"
    while True:
        try:
            resp = redis_sync.xread(
                {K.ORDERS_STREAM_ORDER_EVENTS: last_id},
                count=10,
                block=500,
            )
        except Exception as e:
            log.warning(f"xread orders:stream:order_events failed: {e!r}")
            time.sleep(0.2)
            continue

        if not resp:
            cur_leaf = _read_leaf(redis_sync, position.index, position.instrument_token)
            if cur_leaf:
                cur_bid = float(cur_leaf.get("bid") or 0)
                if cur_bid > 0 and abs(cur_bid - bid) >= 1.0:
                    new_price = round(max(0.05, cur_bid - buffer_inr), 2)
                    UpstoxAPI.modify_order({
                        "order_id": order_id,
                        "access_token": access_token,
                        "price": new_price,
                    })
                    bid = cur_bid
                    submit_price = new_price
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
                    fill_ts = _now_ts_ms()
                    return ExitResult(
                        filled_qty=int(payload.get("filled_qty") or position.qty),
                        avg_fill_price=float(payload.get("avg_price") or submit_price),
                        order_id=order_id,
                        order_events=events,
                        decision_to_exit_submit_ms=0,
                        exit_submit_to_fill_ms=fill_ts - submit_ts,
                    )
                if event_type == "REJECT":
                    # Refresh quote + retry place.
                    cur_leaf = _read_leaf(redis_sync, position.index, position.instrument_token)
                    cur_bid = float((cur_leaf or {}).get("bid") or bid)
                    submit_price = round(max(0.05, cur_bid - buffer_inr), 2)
                    res = UpstoxAPI.place_order({
                        "instrument_token": position.instrument_token,
                        "quantity": int(position.qty),
                        "transaction_type": "SELL",
                        "access_token": access_token,
                        "price": submit_price,
                        "order_type": "LIMIT",
                        "product": "I",
                        "validity": "DAY",
                        "tag": position.pos_id,
                    })
                    if res["success"]:
                        order_id = (res["data"] or {}).get("first_order_id") or order_id


def submit_and_complete(
    redis_sync: _redis_sync.Redis,
    position: Position,
    exit_reason: str,
    *,
    mode: str,
    access_token: str,
    now_hhmm: str,
) -> ExitResult:
    if mode == "paper":
        return submit_and_complete_paper(redis_sync, position, exit_reason, now_hhmm)
    return submit_and_complete_live(redis_sync, position, exit_reason, access_token, now_hhmm)
