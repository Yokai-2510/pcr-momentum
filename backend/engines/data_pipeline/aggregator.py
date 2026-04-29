"""
engines.data_pipeline.aggregator — pure helpers + Redis writers for tick aggregation.

Pure helpers (testable in isolation):
  - update_option_chain_leaf(chain, strike, side, tick) -> chain (in-place + return)
  - update_spot_snapshot(spot, tick, prev_close) -> new spot dict

Redis writers (called by tick_processor):
  - flush_option_chain(redis, index, chain)         — SET full JSON
  - flush_spot(redis, index, spot)                  — HSET fields
  - update_ws_status(redis, ...)                    — HSET market_data:ws_status:market_ws

NOTE: bars / OHLCV / resampler removed. Strategy reads only the live
option_chain leaves; rolling bars were drafted in Project_Plan §5 but never
consumed by Strategy.md or Order Exec. If charting needs OHLCV later, replay
from `metrics_*` Postgres tables.
"""

from __future__ import annotations

from typing import Any

import orjson
import redis.asyncio as _redis_async

from engines.data_pipeline.parser import ParsedTick
from state import keys as K

# ── Pure helpers ─────────────────────────────────────────────────────────


def update_option_chain_leaf(
    chain: dict[str, dict[str, dict[str, Any] | None]],
    strike: int,
    side: str,
    tick: ParsedTick,
) -> dict[str, dict[str, dict[str, Any] | None]]:
    """Merge `tick` into chain[str(strike)][side]; preserve existing fields.

    Leaf shape per Schema.md §1.3:
        {token, ltp, bid, ask, bid_qty, ask_qty, vol, oi, ts}

    Mutates `chain` in place and returns it for convenience.
    """
    side_l = side.lower()
    if side_l not in {"ce", "pe"}:
        return chain
    strike_key = str(strike)
    if strike_key not in chain:
        return chain  # ignore strikes outside the basket window

    existing = chain[strike_key].get(side_l) or {}
    leaf = {
        "token": existing.get("token") or tick.token,
        "ltp": tick.ltp if tick.ltp is not None else existing.get("ltp", 0),
        "bid": tick.bid if tick.bid is not None else existing.get("bid", 0),
        "ask": tick.ask if tick.ask is not None else existing.get("ask", 0),
        "bid_qty": tick.bid_qty if tick.bid_qty is not None else existing.get("bid_qty", 0),
        "ask_qty": tick.ask_qty if tick.ask_qty is not None else existing.get("ask_qty", 0),
        "vol": tick.vol if tick.vol is not None else existing.get("vol", 0),
        "oi": tick.oi if tick.oi is not None else existing.get("oi", 0),
        "ts": tick.ts,
    }
    chain[strike_key][side_l] = leaf
    return chain


def update_spot_snapshot(
    spot: dict[str, Any] | None,
    tick: ParsedTick,
    prev_close: float | None = None,
) -> dict[str, Any]:
    """Compute the spot-index snapshot HASH from a tick."""
    ltp = tick.ltp if tick.ltp is not None else (spot or {}).get("ltp", 0.0)
    pc = prev_close if prev_close is not None else float((spot or {}).get("prev_close", 0.0) or 0.0)
    change_inr = float(ltp) - pc if pc else 0.0
    change_pct = (change_inr / pc * 100.0) if pc else 0.0
    return {
        "ltp": ltp,
        "prev_close": pc,
        "change_inr": round(change_inr, 4),
        "change_pct": round(change_pct, 4),
        "ts": tick.ts,
    }


# ── Redis writers ────────────────────────────────────────────────────────


async def flush_option_chain(
    redis: _redis_async.Redis,
    index: str,
    chain: dict[str, dict[str, dict[str, Any] | None]],
) -> None:
    """Serialize the in-memory option_chain and SET it atomically."""
    await redis.set(K.market_data_index_option_chain(index), orjson.dumps(chain))


async def flush_spot(redis: _redis_async.Redis, index: str, spot: dict[str, Any]) -> None:
    """HSET the spot snapshot fields."""
    if not spot:
        return
    mapping = {k: (str(v) if not isinstance(v, int | float) else v) for k, v in spot.items()}
    await redis.hset(K.market_data_index_spot(index), mapping=mapping)  # type: ignore[misc]


async def update_ws_status(
    redis: _redis_async.Redis,
    *,
    connected: bool,
    last_frame_ts: int | None = None,
    reconnect_count: int | None = None,
) -> None:
    """HSET market_data:ws_status:market_ws fields."""
    fields: dict[str, str | int] = {"connected": "true" if connected else "false"}
    if last_frame_ts is not None:
        fields["last_frame_ts"] = last_frame_ts
    if reconnect_count is not None:
        fields["reconnect_count"] = reconnect_count
    await redis.hset(K.MARKET_DATA_WS_STATUS_MARKET, mapping=fields)  # type: ignore[misc]
