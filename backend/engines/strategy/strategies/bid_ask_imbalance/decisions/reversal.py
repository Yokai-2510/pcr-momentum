"""Reversal warning (Strategy.md §5.4).

Triggers ONLY when ALL FOUR are simultaneously true:

    1. imbalance_drop_pct > drop_threshold over last 3 ticks
    2. ask wall on previously-buying side is now HOLDING or REFRESHING
    3. spread has widened beyond MODERATE
    4. LTP has moved from near-ask to near-bid (or vice versa for PE)

The 4-of-4 conjunction is intentional. Order book data is noisy; firing on
any single condition would whipsaw badly. Only trust it when the entire
picture flips at once.

When triggered:
  - if FLAT  -> emit REVERSAL_WARN telemetry; suppress entries for `suppress_sec` (default 30s)
  - if IN_*  -> emit FLIP signal (exit + immediate re-entry on opposite side, subject to gates)
"""

from __future__ import annotations

from dataclasses import dataclass

from engines.strategy.strategies.bid_ask_imbalance.buffer import StrikeBuffer
from engines.strategy.strategies.bid_ask_imbalance.metrics.ask_wall import (
    WallState,
    classify_wall_state,
)
from engines.strategy.strategies.bid_ask_imbalance.metrics.spread import (
    classify_spread,
    compute_spread,
)
from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg


@dataclass(slots=True, frozen=True)
class ReversalVerdict:
    triggered: bool
    triggers: list[str]
    imbalance_drop_pct: float | None
    new_wall_state: WallState
    spread_widened: bool
    ltp_flipped: bool
    reason: str = ""


def _imbalance_drop_pct(buffer: StrikeBuffer, lookback: int = 3) -> float | None:
    """Return the percentage drop in imbalance from `lookback` ticks ago to now.

    Positive value = imbalance fell (e.g. from 1.5 to 0.9 -> 40% drop).
    Returns None if insufficient history or any value is None.
    """
    history = buffer.last_n(lookback + 1)  # need lookback+1 to span the window
    if len(history) < lookback + 1:
        return None
    earlier = history[0].imbalance
    current = history[-1].imbalance
    if earlier is None or current is None or earlier <= 0:
        return None
    drop = (earlier - current) / earlier * 100.0
    return drop


def evaluate_reversal(
    *,
    held_side: str | None,            # "CE" | "PE" | None (when FLAT, evaluating the dominant strike)
    leg: StrikeLeg,
    buffer: StrikeBuffer,
    imbalance_drop_threshold_pct: float = 30.0,
    spread_good_inr: float,
    spread_moderate_inr: float,
    qty_multiple: float = 5.0,
    aggressor_tolerance_inr: float = 0.10,
    lookback_ticks: int = 3,
) -> ReversalVerdict:
    """Run the 4-of-4 reversal check on a single strike."""
    triggers: list[str] = []

    # 1. Imbalance collapse
    drop_pct = _imbalance_drop_pct(buffer, lookback=lookback_ticks)
    imb_collapsed = drop_pct is not None and drop_pct > imbalance_drop_threshold_pct
    if imb_collapsed:
        triggers.append(f"imbalance_drop_{drop_pct:.1f}%")

    # 2. Wall now HOLDING/REFRESHING (was previously absent or absorbing)
    wall_state = classify_wall_state(
        leg, buffer, qty_multiple=qty_multiple, aggressor_tolerance_inr=aggressor_tolerance_inr
    )
    history = buffer.last_n(lookback_ticks + 1)
    was_clear = any(
        h.ask_wall_present is False or h.ask_wall_present is None for h in history[: max(1, lookback_ticks)]
    )
    wall_blocking = wall_state in ("HOLDING", "REFRESHING")
    wall_just_formed = wall_blocking and was_clear
    if wall_just_formed:
        triggers.append(f"wall_{wall_state.lower()}_formed")

    # 3. Spread widened to AVOID
    spread = compute_spread(leg)
    spread_status = classify_spread(
        spread, good_threshold=spread_good_inr, moderate_threshold=spread_moderate_inr
    )
    spread_widened = spread_status == "AVOID"
    # Compare against history - was it previously GOOD/MODERATE?
    if spread_widened and history:
        prev_spreads = [h.spread for h in history[: max(1, lookback_ticks)] if h.spread is not None]
        if prev_spreads and any(s <= spread_moderate_inr for s in prev_spreads):
            triggers.append("spread_widened")
        else:
            spread_widened = False  # was already wide; not a fresh widening

    # 4. LTP flipped from near-ask to near-bid (CE held), or vice versa (PE held).
    # When FLAT (held_side is None) we use the strike-side as the "expected" buying side.
    expected_aggressor_before = "BUY" if (held_side or leg.side) == "CE" else "SELL"
    flipped_aggressor = "SELL" if expected_aggressor_before == "BUY" else "BUY"
    if leg.ltp is not None and leg.best_bid is not None and leg.best_ask is not None:
        if expected_aggressor_before == "BUY":
            current_at_flipped_side = leg.ltp <= leg.best_bid + aggressor_tolerance_inr
        else:
            current_at_flipped_side = leg.ltp >= leg.best_ask - aggressor_tolerance_inr
    else:
        current_at_flipped_side = False
    ltp_flipped = current_at_flipped_side
    if ltp_flipped:
        triggers.append(f"ltp_flipped_to_{flipped_aggressor.lower()}")

    triggered = imb_collapsed and wall_just_formed and spread_widened and ltp_flipped

    return ReversalVerdict(
        triggered=triggered,
        triggers=triggers,
        imbalance_drop_pct=drop_pct,
        new_wall_state=wall_state,
        spread_widened=spread_widened,
        ltp_flipped=ltp_flipped,
        reason="four_of_four_satisfied" if triggered else "missing_conditions",
    )
