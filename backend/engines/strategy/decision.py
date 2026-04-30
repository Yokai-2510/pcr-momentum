"""
engines.strategy.decision - pure decision functions.

State-specific decision trees from Strategy.md section 6 (entry) and section 8
(reversal). No Redis, no broker, no clock - every dependency is a function
argument.

State enum: FLAT, IN_CE, IN_PE, COOLDOWN, HALTED.
"""

from __future__ import annotations

from typing import Literal

FlatDecision = Literal["BUY_CE", "BUY_PE", "WAIT", "WAIT_RECOVERY"]
InCEDecision = Literal["FLIP_TO_PE", "HOLD"]
InPEDecision = Literal["FLIP_TO_CE", "HOLD"]
CooldownDecision = Literal["CONTINUE_WAIT", "GO_FLAT"]


def decide_when_flat(
    sum_ce: float,
    sum_pe: float,
    delta: float,
    reversal_threshold: float,
    dominance_threshold: float,
) -> FlatDecision:
    """Strategy.md section 6.1 entry decision tree.

    Returns one of:
      BUY_CE         - CE side dominates; emit BUY_CE on highest-Diff CE strike
      BUY_PE         - PE side dominates; emit BUY_PE on highest-Diff PE strike
      WAIT           - both sides positive but neither dominates by gap
      WAIT_RECOVERY  - both sides <= 0; wait for one to cross +reversal_threshold

    Args:
        sum_ce, sum_pe: aggregated strike-Diff per basket (rupees).
        delta:          SUM_PE - SUM_CE (signed; positive means PE leading).
        reversal_threshold:  used for the both-negative recovery cross.
        dominance_threshold: required |delta| when both sides are positive.
    """
    # Both <= 0: WAIT_RECOVERY. The first side to cross +reversal_threshold
    # triggers an entry; that test happens in the caller using the actual SUM
    # values, but the state-machine output here is pure: "do not enter yet".
    if sum_ce <= 0 and sum_pe <= 0:
        # Once a side has crossed +threshold, that side is the entry.
        if sum_ce >= reversal_threshold and sum_ce > sum_pe:
            return "BUY_CE"
        if sum_pe >= reversal_threshold and sum_pe > sum_ce:
            return "BUY_PE"
        return "WAIT_RECOVERY"

    # One side <= 0 and the other > 0: unambiguous entry.
    if sum_ce > 0 and sum_pe <= 0:
        return "BUY_CE"
    if sum_pe > 0 and sum_ce <= 0:
        return "BUY_PE"

    # Both > 0: need dominance gap.
    if abs(delta) > dominance_threshold:
        return "BUY_PE" if delta > 0 else "BUY_CE"
    return "WAIT"


def decide_when_in_ce(delta: float, threshold: float) -> InCEDecision:
    """Flip CE to PE when PE side decisively overtakes.

    Strategy.md section 8.1. delta = SUM_PE - SUM_CE. Positive delta means PE
    is leading.
    """
    if delta > threshold:
        return "FLIP_TO_PE"
    return "HOLD"


def decide_when_in_pe(delta: float, threshold: float) -> InPEDecision:
    """Flip PE to CE when CE side decisively overtakes."""
    if delta < -threshold:
        return "FLIP_TO_CE"
    return "HOLD"


def decide_when_cooldown(now_ts_ms: int, cooldown_until_ts_ms: int) -> CooldownDecision:
    """Pure timer check; transitions COOLDOWN to FLAT when cooldown elapsed."""
    if now_ts_ms >= cooldown_until_ts_ms:
        return "GO_FLAT"
    return "CONTINUE_WAIT"
