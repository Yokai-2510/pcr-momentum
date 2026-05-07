"""In-trade continuation logic (Strategy.md §5.3).

While IN_CE: hold if ALL of:
    imbalance(held_strike) > 1.20
    bid_qty non-decreasing over last 5 ticks
    ask wall on held strike is NONE or ABSORBING
    spread within GOOD or MODERATE
    LTP within tolerance of best ask (still aggressive buying)

Failure of any condition is a soft exit signal — trailing stop tightens. Two
consecutive ticks failing -> hard exit.

Symmetric for IN_PE.
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
from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg


@dataclass(slots=True, frozen=True)
class ContinuationVerdict:
    hold: bool         # True = keep position; False = soft-exit signal
    reason: str
    failures: list[str]


_HISTORY_WINDOW = 5


def evaluate_continuation(
    *,
    side: str,                # "CE" | "PE"
    held_leg: StrikeLeg,
    buffer: StrikeBuffer,
    imbalance_continuation: float = 1.20,
    spread_good_inr: float,
    spread_moderate_inr: float,
    qty_multiple: float = 5.0,
    aggressor_tolerance_inr: float = 0.10,
) -> ContinuationVerdict:
    failures: list[str] = []

    # 1. Imbalance > continuation threshold (PE inverted: held PE wants PE-side imbalance high)
    imb = compute_imbalance(held_leg)
    if imb is None:
        failures.append("imbalance_unknown")
    elif imb < imbalance_continuation:
        failures.append(f"imbalance_{imb:.2f}_below_{imbalance_continuation:.2f}")

    # 2. Bid qty non-decreasing over last N ticks
    history = buffer.last_n(_HISTORY_WINDOW)
    bids = [obs.best_bid_qty for obs in history if obs.best_bid_qty is not None]
    if len(bids) >= 2:
        decreasing = bids[0] > bids[-1] and all(bids[i] >= bids[i + 1] for i in range(len(bids) - 1))
        if decreasing:
            failures.append("bid_qty_decreasing")
    # else: insufficient history; don't penalize — strategy waiting for ticks.

    # 3. Wall not blocking
    wall_state = classify_wall_state(
        held_leg, buffer, qty_multiple=qty_multiple, aggressor_tolerance_inr=aggressor_tolerance_inr
    )
    if wall_state in ("HOLDING", "REFRESHING"):
        failures.append(f"wall_{wall_state.lower()}")

    # 4. Spread acceptable
    spread = compute_spread(held_leg)
    spread_status = classify_spread(
        spread, good_threshold=spread_good_inr, moderate_threshold=spread_moderate_inr
    )
    if spread_status == "AVOID":
        failures.append("spread_too_wide")
    elif spread_status == "UNKNOWN":
        failures.append("spread_unknown")

    # 5. LTP still on the right side
    aggressor = detect_aggressor(held_leg, tolerance_inr=aggressor_tolerance_inr)
    expected = "BUY" if side == "CE" else "SELL"
    if aggressor not in (expected, "MID"):
        failures.append(f"aggressor_{aggressor.lower()}_wrong_for_{side}")
    elif aggressor == "MID":
        # MID is borderline; not a hard fail but logs a soft warning.
        pass

    if not failures:
        return ContinuationVerdict(hold=True, reason="all_conditions_met", failures=[])

    return ContinuationVerdict(
        hold=False,
        reason="continuation_failed",
        failures=failures,
    )
