"""Net Pressure (Strategy.md §4.7).

    net_pressure = cum_ce_imbalance - cum_pe_imbalance

Range / interpretation:

    > +0.50         BULLISH           CE BUY eligible
    > +0.20         DEVELOPING_BULL   no entry, soft bias
    -0.20..+0.20    NEUTRAL           no entry
    < -0.20         DEVELOPING_BEAR   no entry, soft bias
    < -0.50         BEARISH           PE BUY eligible

Returns None if either cumulative is None (book half-empty).
"""

from __future__ import annotations

from typing import Literal

PressureLabel = Literal[
    "BULLISH",
    "DEVELOPING_BULL",
    "NEUTRAL",
    "DEVELOPING_BEAR",
    "BEARISH",
    "UNKNOWN",
]


def net_pressure(cum_ce: float | None, cum_pe: float | None) -> float | None:
    if cum_ce is None or cum_pe is None:
        return None
    return cum_ce - cum_pe


def classify_pressure(
    pressure: float | None,
    *,
    entry_threshold: float = 0.50,
    neutral_band: float = 0.20,
) -> PressureLabel:
    if pressure is None:
        return "UNKNOWN"
    if pressure > entry_threshold:
        return "BULLISH"
    if pressure > neutral_band:
        return "DEVELOPING_BULL"
    if pressure >= -neutral_band:
        return "NEUTRAL"
    if pressure >= -entry_threshold:
        return "DEVELOPING_BEAR"
    return "BEARISH"
