"""
engines.data_pipeline.parser — pure helpers that decode broker WS frames
into a normalized list of `ParsedTick` records.

The Upstox v3 MarketDataStreamerV3 SDK delivers protobuf-decoded dicts to
the on_message callback. The shape varies by mode (`ltpc`, `full`,
`option_greeks`, `full_d30`) and SDK version. We probe defensively and
extract only the fields the aggregator needs:
    token, ltp, bid, ask, bid_qty, ask_qty, vol, oi, ts

A single frame can carry one tick (single-token feed) or many ticks (multi-
feed). `parse_tick` always returns a list (possibly empty) of ParsedTick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ParsedTick:
    token: str
    ltp: float | None
    bid: float | None
    ask: float | None
    bid_qty: int | None
    ask_qty: int | None
    vol: int | None
    oi: int | None
    ts: int  # epoch ms


def _f(v: Any) -> float | None:
    """Coerce to float; return None for missing/junk."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    """Coerce to int; return None for missing/junk."""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _extract_one(token: str, payload: dict[str, Any], default_ts: int) -> ParsedTick:
    """Extract a single ParsedTick from one feed entry. Tolerant of missing fields."""
    # Path candidates (Upstox v3 protobuf shapes):
    #   payload["ltpc"]["ltp"], payload["ltpc"]["ltt"]
    #   payload["fullFeed"]["marketFF"]["ltpc"]["ltp"], etc.
    #   payload["ff"]["marketFF"]["ltpc"]["ltp"]  (alternate alias)
    # We probe in order.
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
    top = bid_ask_quotes[0] if bid_ask_quotes else {}

    market_ohlc = market_ff.get("marketOHLC", {})
    vol = market_ff.get("vtt") or market_ohlc.get("vol") or payload.get("vol") or payload.get("vtt")
    oi = market_ff.get("oi") or payload.get("oi")

    ltt = ltpc.get("ltt") or market_ff.get("tsInMillis") or default_ts
    ts = _i(ltt) or default_ts

    return ParsedTick(
        token=token,
        ltp=_f(ltpc.get("ltp") or ltpc.get("LTP")),
        bid=_f(top.get("bidP") or top.get("bidQuote")),
        ask=_f(top.get("askP") or top.get("askQuote")),
        bid_qty=_i(top.get("bidQ")),
        ask_qty=_i(top.get("askQ")),
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
        # Possibly already flat — but only if the top-level keys look like tokens.
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
