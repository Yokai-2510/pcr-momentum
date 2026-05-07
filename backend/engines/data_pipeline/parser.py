"""
engines.data_pipeline.parser — pure helpers that decode broker WS frames
into a normalized list of `ParsedTick` records.

Upstox v3 MarketDataStreamerV3 (mode="full") delivers protobuf-decoded dicts
with FULL 5-level market depth. Earlier versions of this parser kept only
top-of-book; the bid/ask imbalance strategy (Strategy.md) needs the entire
book, so we now extract:

    token, ltp,
    best_bid, best_ask, best_bid_qty, best_ask_qty,
    bid_prices_l1..l5, bid_qtys_l1..l5,
    ask_prices_l1..l5, ask_qtys_l1..l5,
    total_bid_qty, total_ask_qty,
    vol, oi, ts

Empty/missing depth levels are represented as None — never as 0 (which is a
valid quantity).

Pure module: no I/O, no logging, no Redis. Fully unit-testable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

DEPTH_LEVELS = 5


@dataclass(slots=True, frozen=True)
class ParsedTick:
    token: str
    ltp: float | None
    # Top-of-book convenience fields (= level 1 of the arrays below).
    bid: float | None
    ask: float | None
    bid_qty: int | None
    ask_qty: int | None
    # Full 5-level depth. Each list is exactly DEPTH_LEVELS long, padded with None.
    bid_prices: tuple[float | None, ...] = field(default_factory=lambda: (None,) * DEPTH_LEVELS)
    ask_prices: tuple[float | None, ...] = field(default_factory=lambda: (None,) * DEPTH_LEVELS)
    bid_qtys: tuple[int | None, ...] = field(default_factory=lambda: (None,) * DEPTH_LEVELS)
    ask_qtys: tuple[int | None, ...] = field(default_factory=lambda: (None,) * DEPTH_LEVELS)
    # Cumulative quantities — sum across the depth levels exposed by the broker.
    total_bid_qty: int | None = None
    total_ask_qty: int | None = None
    # Other.
    vol: int | None = None
    oi: int | None = None
    ts: int = 0  # epoch ms


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f or f == float("inf") or f == float("-inf"):  # NaN / inf guard
            return None
        return f
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _pad(seq: list, n: int, fill: Any = None) -> tuple:
    """Right-pad / truncate a list to length n; return immutable tuple."""
    if len(seq) >= n:
        return tuple(seq[:n])
    return tuple(seq) + (fill,) * (n - len(seq))


def _extract_depth(
    bid_ask_quotes: list[dict[str, Any]],
) -> tuple[tuple, tuple, tuple, tuple, int | None, int | None]:
    """Pull 5-level depth out of the bidAskQuote list.

    Each entry has the shape:
        {"bidQ": int, "bidP": float, "askQ": int, "askP": float, "bidOrders": int, ...}
    Some Upstox SDK versions use `bidQuote` / `askQuote` as the price aliases.

    Returns: (bid_prices, ask_prices, bid_qtys, ask_qtys, total_bid_qty, total_ask_qty)
    where each *_prices/*_qtys tuple is exactly DEPTH_LEVELS long.
    """
    bid_prices: list[float | None] = []
    ask_prices: list[float | None] = []
    bid_qtys: list[int | None] = []
    ask_qtys: list[int | None] = []

    for entry in bid_ask_quotes[:DEPTH_LEVELS]:
        if not isinstance(entry, dict):
            bid_prices.append(None)
            ask_prices.append(None)
            bid_qtys.append(None)
            ask_qtys.append(None)
            continue
        bid_prices.append(_f(entry.get("bidP") or entry.get("bidQuote")))
        ask_prices.append(_f(entry.get("askP") or entry.get("askQuote")))
        bid_qtys.append(_i(entry.get("bidQ")))
        ask_qtys.append(_i(entry.get("askQ")))

    bid_prices_t = _pad(bid_prices, DEPTH_LEVELS)
    ask_prices_t = _pad(ask_prices, DEPTH_LEVELS)
    bid_qtys_t = _pad(bid_qtys, DEPTH_LEVELS)
    ask_qtys_t = _pad(ask_qtys, DEPTH_LEVELS)

    # Total = sum of available levels (treat None as 0 for the sum, but only
    # if at least one level is present; else return None).
    bq_present = [q for q in bid_qtys_t if q is not None]
    aq_present = [q for q in ask_qtys_t if q is not None]
    total_bid = sum(bq_present) if bq_present else None
    total_ask = sum(aq_present) if aq_present else None

    return bid_prices_t, ask_prices_t, bid_qtys_t, ask_qtys_t, total_bid, total_ask


def _extract_one(token: str, payload: dict[str, Any], default_ts: int) -> ParsedTick:
    """Extract a single ParsedTick from one feed entry. Tolerant of missing fields."""
    ltpc = (
        payload.get("ltpc")
        or payload.get("fullFeed", {}).get("marketFF", {}).get("ltpc")
        or payload.get("ff", {}).get("marketFF", {}).get("ltpc")
        or payload.get("fullFeed", {}).get("indexFF", {}).get("ltpc")
        or payload.get("ff", {}).get("indexFF", {}).get("ltpc")
        or {}
    )

    market_ff = (
        payload.get("fullFeed", {}).get("marketFF")
        or payload.get("ff", {}).get("marketFF")
        or payload.get("marketFF")
        or {}
    )

    market_level = market_ff.get("marketLevel") or payload.get("marketLevel") or {}
    bid_ask_quotes = market_level.get("bidAskQuote") or []

    bid_prices, ask_prices, bid_qtys, ask_qtys, total_bid, total_ask = _extract_depth(
        bid_ask_quotes
    )

    market_ohlc = market_ff.get("marketOHLC", {})
    vol = market_ff.get("vtt") or market_ohlc.get("vol") or payload.get("vol") or payload.get("vtt")
    oi = market_ff.get("oi") or payload.get("oi")

    # `tbq` / `tsq` (Upstox aggregates across the entire book) take precedence
    # over our level-summed totals if the broker provides them.
    tbq_explicit = _i(market_ff.get("tbq") or payload.get("tbq"))
    tsq_explicit = _i(market_ff.get("tsq") or payload.get("tsq"))
    if tbq_explicit is not None:
        total_bid = tbq_explicit
    if tsq_explicit is not None:
        total_ask = tsq_explicit

    ltt = ltpc.get("ltt") or market_ff.get("tsInMillis") or default_ts
    ts = _i(ltt) or default_ts

    return ParsedTick(
        token=token,
        ltp=_f(ltpc.get("ltp") or ltpc.get("LTP")),
        bid=bid_prices[0],
        ask=ask_prices[0],
        bid_qty=bid_qtys[0],
        ask_qty=ask_qtys[0],
        bid_prices=bid_prices,
        ask_prices=ask_prices,
        bid_qtys=bid_qtys,
        ask_qtys=ask_qtys,
        total_bid_qty=total_bid,
        total_ask_qty=total_ask,
        vol=_i(vol),
        oi=_i(oi),
        ts=ts,
    )


def parse_tick(raw_frame: dict[str, Any]) -> list[ParsedTick]:
    """Decode one broker WS frame into 0..N ParsedTicks.

    Accepts:
      - {"feeds": {"<token>": {<payload>}, ...}}        (Upstox v3 typical)
      - {"<token>": {<payload>}, ...}                    (flattened)
      - {"type": "live_feed", "feeds": {...}}            (with type wrapper)
    """
    if not isinstance(raw_frame, dict):
        return []

    default_ts = int(time.time() * 1000)

    feeds = raw_frame.get("feeds")
    if not isinstance(feeds, dict):
        if all(isinstance(k, str) and "|" in k for k in raw_frame):
            feeds = raw_frame
        else:
            return []

    out: list[ParsedTick] = []
    for token, payload in feeds.items():
        if not isinstance(token, str) or not isinstance(payload, dict):
            continue
        out.append(_extract_one(token, payload, default_ts))
    return out


def tick_to_chain_leg(tick: ParsedTick) -> dict[str, Any]:
    """Serialize a ParsedTick into the JSON shape used by option_chain payloads.

    The Strategy.md §9.1 contract: every basket leg in
    `market_data:indexes:{idx}:option_chain` carries the full 5-level depth so
    a vessel's `evaluate(snapshot)` can compute imbalance + spread + ask wall
    without consulting the tick stream.
    """
    return {
        "token": tick.token,
        "ltp": tick.ltp,
        "bid": tick.bid,
        "ask": tick.ask,
        "bid_qty": tick.bid_qty,
        "ask_qty": tick.ask_qty,
        "bid_prices": list(tick.bid_prices),
        "ask_prices": list(tick.ask_prices),
        "bid_qtys": list(tick.bid_qtys),
        "ask_qtys": list(tick.ask_qtys),
        "total_bid_qty": tick.total_bid_qty,
        "total_ask_qty": tick.total_ask_qty,
        "vol": tick.vol,
        "oi": tick.oi,
        "ts": tick.ts,
    }
