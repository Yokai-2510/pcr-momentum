"""Ask wall detection + sub-state classification (Strategy.md §4.3).

Wall present?

    best_ask_qty > qty_multiple * best_bid_qty   (default qty_multiple=5)

Wall sub-state requires history (last ~10 ticks, from the buffer):

    HOLDING      wall_qty has not decreased over the last 5 ticks.
                 Acts as resistance — do not enter aggressively.

    ABSORBING    wall_qty is monotonically decreasing AND LTP near ask AND
                 bid_qty rising. High-conviction breakout.

    REFRESHING   wall_qty resets to ~original size after each absorption
                 attempt. Hidden algo seller. Stronger than HOLDING — hard
                 exit signal for any in-flight CE position.

Symmetric for bid walls (PE side reads ask_wall on PE leg, since the same
formula applies — buyer pressure on a PE means resistance to PE move).
"""

from __future__ import annotations

from typing import Literal

from engines.strategy.strategies.bid_ask_imbalance.buffer import (
    StrikeBuffer,
    TickObservation,
)
from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg

WallState = Literal["NONE", "HOLDING", "ABSORBING", "REFRESHING", "UNKNOWN"]

_HISTORY_WINDOW = 5  # ticks back for trend detection
_REFRESH_TOLERANCE = 0.85  # qty restored to >=85% of peak = "refreshing"


def is_wall_present(leg: StrikeLeg, *, qty_multiple: float = 5.0) -> bool | None:
    """Return True if best_ask_qty > qty_multiple * best_bid_qty."""
    if leg.best_ask_qty is None or leg.best_bid_qty is None:
        return None
    if leg.best_bid_qty <= 0:
        # Empty bid side — wall is technically present (infinite ratio).
        return leg.best_ask_qty > 0
    return leg.best_ask_qty > qty_multiple * leg.best_bid_qty


def classify_wall_state(
    leg: StrikeLeg,
    buffer: StrikeBuffer,
    *,
    qty_multiple: float = 5.0,
    aggressor_tolerance_inr: float = 0.10,
) -> WallState:
    """Classify the wall using current leg + recent history."""
    present = is_wall_present(leg, qty_multiple=qty_multiple)
    if present is None:
        return "UNKNOWN"
    if not present:
        return "NONE"

    history = buffer.last_n(_HISTORY_WINDOW)
    if len(history) < 2:
        # Not enough history to distinguish HOLDING from ABSORBING/REFRESHING.
        return "HOLDING"

    # Walk through historical ask qty values for this strike.
    ask_qtys = [obs.best_ask_qty for obs in history if obs.best_ask_qty is not None]
    if len(ask_qtys) < 2:
        return "HOLDING"

    peak_qty = max(ask_qtys)
    current_qty = leg.best_ask_qty or 0
    decreasing = all(ask_qtys[i] >= ask_qtys[i + 1] for i in range(len(ask_qtys) - 1))

    # REFRESHING: qty was previously absorbed (dipped) but is now ~back to peak.
    dipped = min(ask_qtys) < peak_qty * 0.7
    restored = current_qty >= peak_qty * _REFRESH_TOLERANCE
    if dipped and restored:
        return "REFRESHING"

    # ABSORBING: monotonic decrease + LTP at ask + bid_qty rising.
    if decreasing and leg.best_ask is not None and leg.ltp is not None:
        ltp_near_ask = leg.ltp >= leg.best_ask - aggressor_tolerance_inr
        bid_rising = (
            leg.best_bid_qty is not None
            and history[0].best_bid_qty is not None
            and leg.best_bid_qty > history[0].best_bid_qty
        )
        if ltp_near_ask and bid_rising:
            return "ABSORBING"

    return "HOLDING"


def cache_observation_imbalance(
    leg: StrikeLeg,
    imbalance: float | None,
    spread: float | None,
    wall_present: bool | None,
    aggressor: str | None,
    ts_fallback: int,
) -> TickObservation:
    """Build a TickObservation suitable for `StrikeBuffer.push(...)`."""
    return TickObservation(
        ts=leg.ts or ts_fallback,
        ltp=leg.ltp,
        best_bid=leg.best_bid,
        best_ask=leg.best_ask,
        best_bid_qty=leg.best_bid_qty,
        best_ask_qty=leg.best_ask_qty,
        total_bid_qty=leg.total_bid_qty,
        total_ask_qty=leg.total_ask_qty,
        imbalance=imbalance,
        spread=spread,
        ask_wall_present=wall_present,
        aggressor=aggressor,
    )
