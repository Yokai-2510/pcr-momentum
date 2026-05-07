"""Spread + classification (Strategy.md §4.2).

    spread(strike) = best_ask - best_bid

Per-instrument thresholds:

    NIFTY      good <= 0.50    moderate <= 1.00    avoid > 1.00
    BANKNIFTY  good <= 1.50    moderate <= 3.00    avoid > 3.00
    SENSEX     good <= 1.00    moderate <= 2.00    avoid > 2.00

Classification is consumed by the spread quality gate (§5.2 Gate 3) and the
quality score (§4.8).
"""

from __future__ import annotations

from typing import Literal

from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg

SpreadStatus = Literal["GOOD", "MODERATE", "AVOID", "UNKNOWN"]


def compute_spread(leg: StrikeLeg) -> float | None:
    if leg.best_bid is None or leg.best_ask is None:
        return None
    spread = leg.best_ask - leg.best_bid
    if spread < 0:
        # Crossed book — broker glitch. Treat as no-signal.
        return None
    return spread


def classify_spread(
    spread: float | None,
    *,
    good_threshold: float,
    moderate_threshold: float,
) -> SpreadStatus:
    if spread is None:
        return "UNKNOWN"
    if spread <= good_threshold:
        return "GOOD"
    if spread <= moderate_threshold:
        return "MODERATE"
    return "AVOID"
