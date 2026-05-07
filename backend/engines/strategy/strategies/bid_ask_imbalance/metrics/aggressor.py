"""LTP aggressor detection (Strategy.md §4.4).

    if LTP >= best_ask - tolerance:  aggressive_buying = True
    if LTP <= best_bid + tolerance:  aggressive_selling = True

Tolerance default is 0.10 INR; instrument-overrideable via
`strategy:configs:strategies:{sid}.thresholds.ltp_aggressor_tolerance_inr`.

Aggressor state is one of:
    BUY        LTP near ask — lifted by buyers
    SELL       LTP near bid — hit by sellers
    MID        LTP between bid and ask, no clear aggressor
    UNKNOWN    bid/ask/ltp missing
"""

from __future__ import annotations

from typing import Literal

from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg

Aggressor = Literal["BUY", "SELL", "MID", "UNKNOWN"]


def detect_aggressor(leg: StrikeLeg, *, tolerance_inr: float = 0.10) -> Aggressor:
    if leg.ltp is None or leg.best_bid is None or leg.best_ask is None:
        return "UNKNOWN"
    if leg.ltp >= leg.best_ask - tolerance_inr:
        return "BUY"
    if leg.ltp <= leg.best_bid + tolerance_inr:
        return "SELL"
    return "MID"
