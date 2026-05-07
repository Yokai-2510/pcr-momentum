"""Per-strike imbalance ratio (Strategy.md §4.1).

    imbalance(strike) = total_bid_qty / total_ask_qty

`total_bid_qty` / `total_ask_qty` are sums across the 5 depth levels exposed
by the broker. Provided directly by the parser in `StrikeLeg.total_bid_qty`
and `total_ask_qty`.

Returns None when:
  - either total is missing or zero
  - either side is None (book half-empty)

A ratio of None is the right value to propagate; downstream code MUST treat
None as "no signal" (not 0, not 1.0).
"""

from __future__ import annotations

from typing import Literal

from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg

Classification = Literal[
    "STRONG_BUYERS",
    "MODERATE_BUYERS",
    "NEUTRAL",
    "MODERATE_SELLERS",
    "STRONG_SELLERS",
    "UNKNOWN",
]


def compute_imbalance(leg: StrikeLeg) -> float | None:
    """Return ΣBidQty / ΣAskQty, or None if not computable."""
    if leg.total_bid_qty is None or leg.total_ask_qty is None:
        return None
    if leg.total_ask_qty <= 0:
        return None
    if leg.total_bid_qty < 0:
        return None
    return leg.total_bid_qty / leg.total_ask_qty


def classify_imbalance(
    ratio: float | None,
    *,
    strong_buy: float = 1.30,
    moderate_buy: float = 1.10,
    neutral_low: float = 0.90,
    moderate_sell: float = 0.70,
) -> Classification:
    """Classify an imbalance ratio into one of 6 buckets.

    Defaults are from Strategy.md §4.1 / Section 10.1 thresholds.
    """
    if ratio is None:
        return "UNKNOWN"
    if ratio > strong_buy:
        return "STRONG_BUYERS"
    if ratio > moderate_buy:
        return "MODERATE_BUYERS"
    if ratio >= neutral_low:
        return "NEUTRAL"
    if ratio >= moderate_sell:
        return "MODERATE_SELLERS"
    return "STRONG_SELLERS"
