"""4-gate entry sequence (Strategy.md §5.2).

Gate 1  Direction       |net_pressure| > entry_threshold (default 0.5)
Gate 2  Ask wall         must be ABSENT or ABSORBING on chosen-side dominant strike
Gate 3  Spread           must be GOOD (or MODERATE -> half size)
Gate 4  Quality score    >= phase-specific min from timing.py

Each gate is a pure function. The orchestrator calls them in order; failing
any gate aborts the entry attempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from engines.strategy.strategies.bid_ask_imbalance.metrics.ask_wall import WallState
from engines.strategy.strategies.bid_ask_imbalance.metrics.spread import SpreadStatus


@dataclass(slots=True, frozen=True)
class GateResult:
    passed: bool
    reason: str = ""
    side: str | None = None  # set by Gate 1 if passed
    size_factor: float = 1.0  # adjusted by Gate 3 if MODERATE spread


def gate1_direction(
    net_pressure: float | None,
    *,
    entry_threshold: float = 0.50,
) -> GateResult:
    """Direction filter from net pressure."""
    if net_pressure is None:
        return GateResult(False, "net_pressure_unknown")
    if net_pressure > entry_threshold:
        return GateResult(True, side="CE")
    if net_pressure < -entry_threshold:
        return GateResult(True, side="PE")
    return GateResult(False, f"net_pressure_{net_pressure:.2f}_below_threshold_{entry_threshold:.2f}")


def gate2_ask_wall(wall_state: WallState) -> GateResult:
    """Ask wall on chosen-side dominant strike must not be blocking."""
    if wall_state == "UNKNOWN":
        return GateResult(False, "ask_wall_unknown")
    if wall_state in ("HOLDING", "REFRESHING"):
        return GateResult(False, f"ask_wall_{wall_state.lower()}_blocks_entry")
    return GateResult(True)


def gate3_spread(spread_status: SpreadStatus) -> GateResult:
    """Spread quality. MODERATE passes but with half-size."""
    if spread_status == "UNKNOWN":
        return GateResult(False, "spread_unknown")
    if spread_status == "AVOID":
        return GateResult(False, "spread_too_wide")
    if spread_status == "MODERATE":
        return GateResult(True, "spread_moderate_half_size", size_factor=0.5)
    return GateResult(True)


def gate4_quality_score(score: int, min_score: int) -> GateResult:
    """Score must clear the phase-specific minimum."""
    if score < min_score:
        return GateResult(False, f"score_{score}_below_min_{min_score}")
    return GateResult(True)
