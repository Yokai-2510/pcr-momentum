"""
Snapshot — typed view of the basket-token state at one instant.

The runner reads `market_data:indexes:{idx}:option_chain` (a JSON STRING) and
the basket spec (vessel basket = 3..10 CE strikes + 3..10 PE strikes around
ATM), then constructs a `Snapshot` for the strategy to evaluate.

The strategy never reads Redis directly; it reads `Snapshot` and a per-vessel
`MemoryStore`. This is what makes every `on_tick(...)` call a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class StrikeLeg:
    """One side (CE or PE) of one strike. Values may be None on first read."""

    token: str
    strike: int
    side: str  # "CE" | "PE"
    ltp: float | None
    best_bid: float | None
    best_ask: float | None
    best_bid_qty: int | None
    best_ask_qty: int | None
    bid_qtys: tuple[int | None, ...]
    ask_qtys: tuple[int | None, ...]
    total_bid_qty: int | None
    total_ask_qty: int | None
    vol: int | None
    oi: int | None
    ts: int


@dataclass(slots=True, frozen=True)
class Snapshot:
    """A single point-in-time view of the basket. Built on every tick."""

    instrument_id: str
    atm: int
    spot: float | None
    spot_ts: int
    ce_legs: tuple[StrikeLeg, ...]
    pe_legs: tuple[StrikeLeg, ...]
    snapshot_ts: int  # ms

    @property
    def all_legs(self) -> tuple[StrikeLeg, ...]:
        return self.ce_legs + self.pe_legs


def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _build_leg(
    token: str,
    strike: int,
    side: str,
    leaf: dict[str, Any] | None,
) -> StrikeLeg:
    if not isinstance(leaf, dict):
        return StrikeLeg(
            token=token,
            strike=strike,
            side=side,
            ltp=None,
            best_bid=None,
            best_ask=None,
            best_bid_qty=None,
            best_ask_qty=None,
            bid_qtys=(None,) * 5,
            ask_qtys=(None,) * 5,
            total_bid_qty=None,
            total_ask_qty=None,
            vol=None,
            oi=None,
            ts=0,
        )

    bid_qtys_raw = leaf.get("bid_qtys") or [None] * 5
    ask_qtys_raw = leaf.get("ask_qtys") or [None] * 5
    bid_qtys = tuple(_coerce_int(q) for q in bid_qtys_raw)[:5]
    ask_qtys = tuple(_coerce_int(q) for q in ask_qtys_raw)[:5]
    # Pad in case the broker sent fewer than 5 levels
    bid_qtys = bid_qtys + (None,) * (5 - len(bid_qtys))
    ask_qtys = ask_qtys + (None,) * (5 - len(ask_qtys))

    return StrikeLeg(
        token=str(leaf.get("token") or token),
        strike=strike,
        side=side,
        ltp=_coerce_float(leaf.get("ltp")),
        best_bid=_coerce_float(leaf.get("bid")),
        best_ask=_coerce_float(leaf.get("ask")),
        best_bid_qty=_coerce_int(leaf.get("bid_qty")),
        best_ask_qty=_coerce_int(leaf.get("ask_qty")),
        bid_qtys=bid_qtys,
        ask_qtys=ask_qtys,
        total_bid_qty=_coerce_int(leaf.get("total_bid_qty")),
        total_ask_qty=_coerce_int(leaf.get("total_ask_qty")),
        vol=_coerce_int(leaf.get("vol")),
        oi=_coerce_int(leaf.get("oi")),
        ts=_coerce_int(leaf.get("ts")) or 0,
    )


def build_snapshot(
    *,
    instrument_id: str,
    atm: int,
    basket_ce: list[tuple[int, str]],   # [(strike, token), ...]
    basket_pe: list[tuple[int, str]],
    option_chain: dict[str, Any],
    spot: dict[str, Any] | None,
    snapshot_ts: int,
) -> Snapshot:
    """Build a Snapshot from raw Redis data.

    `option_chain` has shape:
        {strike_str: {"ce": {leaf...}, "pe": {leaf...}}}

    Each leaf carries the full 5-level depth fields written by the data
    pipeline (Strategy.md §9.1).
    """
    ce_legs: list[StrikeLeg] = []
    for strike, token in basket_ce:
        leaf = (option_chain.get(str(strike)) or {}).get("ce")
        # Tolerate token-mismatch (broker may return same strike with a
        # different instrument key after expiry rolls); we trust the basket.
        ce_legs.append(_build_leg(token, strike, "CE", leaf))

    pe_legs: list[StrikeLeg] = []
    for strike, token in basket_pe:
        leaf = (option_chain.get(str(strike)) or {}).get("pe")
        pe_legs.append(_build_leg(token, strike, "PE", leaf))

    spot_val = _coerce_float((spot or {}).get("ltp"))
    spot_ts = _coerce_int((spot or {}).get("ts")) or 0

    return Snapshot(
        instrument_id=instrument_id,
        atm=atm,
        spot=spot_val,
        spot_ts=spot_ts,
        ce_legs=tuple(ce_legs),
        pe_legs=tuple(pe_legs),
        snapshot_ts=snapshot_ts,
    )
