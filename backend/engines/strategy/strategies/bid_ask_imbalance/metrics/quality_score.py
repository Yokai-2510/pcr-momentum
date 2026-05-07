"""Execution quality score (Strategy.md §4.8).

5 conditions x 2 points = 10 max:

  1. Spread on chosen-side dominant strike is GOOD                         +2
  2. Per-strike imbalance on dominant strike > strong_buy threshold        +2
  3. Ask wall on chosen side is ABSENT or ABSORBING (not HOLDING/REFRESHING)  +2
  4. Tick speed: 3+ consecutive upticks (CE) / downticks (PE) within 1 s   +2
  5. LTP near ask (CE) / near bid (PE)                                     +2

Score interpretation:
    8-10  Aggressive entry — market or near-ask limit
    5-7   Moderate entry — limit at mid; 50% size
    < 5   No entry
"""

from __future__ import annotations

from dataclasses import dataclass

from engines.strategy.strategies.bid_ask_imbalance.buffer import StrikeBuffer
from engines.strategy.strategies.bid_ask_imbalance.metrics.aggressor import detect_aggressor
from engines.strategy.strategies.bid_ask_imbalance.metrics.ask_wall import classify_wall_state
from engines.strategy.strategies.bid_ask_imbalance.metrics.imbalance import compute_imbalance
from engines.strategy.strategies.bid_ask_imbalance.metrics.spread import (
    classify_spread,
    compute_spread,
)
from engines.strategy.strategies.bid_ask_imbalance.metrics.tick_speed import (
    consecutive_downticks,
    consecutive_upticks,
)
from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg


@dataclass(slots=True, frozen=True)
class QualityResult:
    score: int
    breakdown: dict[str, int]
    entry_size_factor: float  # 1.0, 0.5, or 0.0


def compute_quality_score(
    *,
    side: str,                       # "CE" | "PE"
    dominant_leg: StrikeLeg,
    buffer: StrikeBuffer,
    spread_good_inr: float,
    spread_moderate_inr: float,
    imbalance_strong_buy: float,
    qty_multiple: float,
    tick_min_consecutive: int,
    tick_window_ms: int,
    aggressor_tolerance_inr: float,
) -> QualityResult:
    """Compute the 0-10 score for a single dominant strike."""
    breakdown: dict[str, int] = {
        "spread": 0,
        "imbalance": 0,
        "ask_wall": 0,
        "tick_speed": 0,
        "ltp_position": 0,
    }

    # 1. Spread good
    spread = compute_spread(dominant_leg)
    spread_status = classify_spread(
        spread, good_threshold=spread_good_inr, moderate_threshold=spread_moderate_inr
    )
    if spread_status == "GOOD":
        breakdown["spread"] = 2

    # 2. Imbalance dominant on chosen side
    imb = compute_imbalance(dominant_leg)
    if imb is not None:
        if side == "CE" and imb > imbalance_strong_buy:
            breakdown["imbalance"] = 2
        elif side == "PE" and imb < (1.0 / imbalance_strong_buy if imbalance_strong_buy > 0 else 0.7):
            # Symmetric on PE — strong sellers in CE = strong buyers in PE proxy.
            # The PE leg's own imbalance (>1.3 means PE buyers loading) is a
            # better signal — use that instead.
            breakdown["imbalance"] = 2
        elif side == "PE" and imb > imbalance_strong_buy:
            breakdown["imbalance"] = 2

    # 3. Ask wall not blocking
    wall_state = classify_wall_state(
        dominant_leg,
        buffer,
        qty_multiple=qty_multiple,
        aggressor_tolerance_inr=aggressor_tolerance_inr,
    )
    if wall_state in ("NONE", "ABSORBING"):
        breakdown["ask_wall"] = 2

    # 4. Tick speed (consecutive directional)
    if side == "CE":
        streak = consecutive_upticks(buffer, window_ms=tick_window_ms)
    else:
        streak = consecutive_downticks(buffer, window_ms=tick_window_ms)
    if streak >= tick_min_consecutive:
        breakdown["tick_speed"] = 2

    # 5. LTP position
    aggressor = detect_aggressor(dominant_leg, tolerance_inr=aggressor_tolerance_inr)
    if (side == "CE" and aggressor == "BUY") or (side == "PE" and aggressor == "SELL"):
        breakdown["ltp_position"] = 2

    score = sum(breakdown.values())

    if score >= 8:
        size_factor = 1.0
    elif score >= 5:
        size_factor = 0.5
    else:
        size_factor = 0.0

    return QualityResult(score=score, breakdown=breakdown, entry_size_factor=size_factor)
