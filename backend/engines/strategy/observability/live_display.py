"""Live display payload (Strategy.md §11.2).

Throttled to ~once every 2 seconds per vessel. Reads the latest metrics from
Redis and writes a formatted text block to a dedicated `pcr-strategy-live`
log stream + a JSON variant to `ui:views:vessels:{sid}:{idx}` for the
frontend dashboard to consume.

The same block can be rendered as a journalctl-friendly multiline string
(operator visibility on SSH) and as a structured object for the WS push.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K

_IST = ZoneInfo("Asia/Kolkata")
_REFRESH_INTERVAL_SEC = 2.0


def _decode(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode()
    return str(v)


def _format_block(sid: str, idx: str, payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics", {}) or {}
    per_strike = metrics.get("per_strike", {}) or {}
    ts = datetime.fromtimestamp(int(payload.get("ts_ms", 0)) / 1000.0, _IST).strftime("%H:%M:%S")
    state = payload.get("state", "UNKNOWN")
    np_value = metrics.get("net_pressure")
    np_label = metrics.get("net_pressure_label", "—")
    cum_ce = metrics.get("cum_ce_imbalance")
    cum_pe = metrics.get("cum_pe_imbalance")
    spot = metrics.get("spot")
    atm = metrics.get("atm")

    # Pick the strike with the highest CE imbalance for the live block
    ce_strikes = sorted(
        (s for s in per_strike.values() if s.get("side") == "CE" and s.get("imbalance") is not None),
        key=lambda x: x.get("imbalance") or 0,
        reverse=True,
    )
    pe_strikes = sorted(
        (s for s in per_strike.values() if s.get("side") == "PE" and s.get("imbalance") is not None),
        key=lambda x: x.get("imbalance") or 0,
        reverse=True,
    )
    ce_top = ce_strikes[0] if ce_strikes else {}
    pe_top = pe_strikes[0] if pe_strikes else {}

    last_action = payload.get("action", "—")
    last_reason = payload.get("reason", "")
    score = payload.get("score")
    return (
        f"===== LIVE DEPTH ENGINE [{sid} | {idx}] =====\n"
        f"Time:           {ts} IST\n"
        f"State:          {state}    ATM: {atm}    Spot: {spot}\n"
        f"\n"
        f"CE side  cum_imb={cum_ce}    dominant_strike={ce_top.get('strike')}\n"
        f"  imb={ce_top.get('imbalance')} spread={ce_top.get('spread')} "
        f"({ce_top.get('spread_class')}) wall={ce_top.get('wall_state')} "
        f"aggressor={ce_top.get('aggressor')}\n"
        f"\n"
        f"PE side  cum_imb={cum_pe}    dominant_strike={pe_top.get('strike')}\n"
        f"  imb={pe_top.get('imbalance')} spread={pe_top.get('spread')} "
        f"({pe_top.get('spread_class')}) wall={pe_top.get('wall_state')} "
        f"aggressor={pe_top.get('aggressor')}\n"
        f"\n"
        f"NET PRESSURE:   {np_value}  [{np_label}]\n"
        f"LAST ACTION:    {last_action}    reason: {last_reason}\n"
        f"QUALITY SCORE:  {score}\n"
        f"================================================\n"
    )


async def display_loop(
    redis_async: _redis_async.Redis,
    redis_sync: Any,
    *,
    vessel_keys: list[tuple[str, str]],
    shutdown: Any,
) -> None:
    """Periodically format + publish the live-display block per vessel."""
    log = logger.bind(engine="strategy", component="live_display")
    while not shutdown.is_set():
        for sid, idx in vessel_keys:
            try:
                last_dec_raw = redis_sync.get(K.vessel_metrics_last_decision(sid, idx))
                if not last_dec_raw:
                    continue
                last_dec = orjson.loads(last_dec_raw if isinstance(last_dec_raw, bytes) else last_dec_raw.encode())
                metrics_per_strike_raw = redis_sync.get(K.vessel_metrics_per_strike(sid, idx))
                per_strike = (
                    orjson.loads(metrics_per_strike_raw)
                    if isinstance(metrics_per_strike_raw, (bytes, str)) and metrics_per_strike_raw
                    else {}
                )
                np_raw = _decode(redis_sync.get(K.vessel_metrics_net_pressure(sid, idx)))
                cum_ce_raw = _decode(redis_sync.get(K.vessel_metrics_cum_ce(sid, idx)))
                cum_pe_raw = _decode(redis_sync.get(K.vessel_metrics_cum_pe(sid, idx)))
                state = _decode(redis_sync.get(K.vessel_state(sid, idx)))
                basket_raw = redis_sync.get(K.vessel_basket(sid, idx))
                basket = (
                    orjson.loads(basket_raw)
                    if isinstance(basket_raw, (bytes, str)) and basket_raw
                    else {}
                )

                payload: dict[str, Any] = {
                    "strategy_id": sid,
                    "instrument_id": idx,
                    "state": state,
                    "ts_ms": int(time.time() * 1000),
                    "action": last_dec.get("action"),
                    "reason": last_dec.get("reason"),
                    "score": last_dec.get("score"),
                    "score_breakdown": last_dec.get("score_breakdown"),
                    "metrics": {
                        "net_pressure": float(np_raw) if np_raw else None,
                        "cum_ce_imbalance": float(cum_ce_raw) if cum_ce_raw else None,
                        "cum_pe_imbalance": float(cum_pe_raw) if cum_pe_raw else None,
                        "atm": basket.get("atm"),
                        "spot": None,
                        "per_strike": per_strike,
                    },
                }
                # Push to UI key + log block.
                await redis_async.set(K.ui_view_vessel(sid, idx), orjson.dumps(payload))
                await redis_async.publish(K.UI_PUB_VIEW, K.ui_view_vessel(sid, idx))
                log.bind(component="live_display", sid=sid, idx=idx).debug(
                    "\n" + _format_block(sid, idx, payload)
                )
            except Exception as exc:
                log.warning(f"display_loop {sid}:{idx} failed: {exc!r}")

        try:
            import asyncio
            await asyncio.wait_for(shutdown.wait(), timeout=_REFRESH_INTERVAL_SEC)
        except (TimeoutError, Exception):
            continue
    log.info("display_loop: shutdown")
