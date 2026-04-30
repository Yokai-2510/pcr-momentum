"""
engines.order_exec.exit_eval — Stage D.

Eight-trigger priority cascade per Strategy.md §7.1. The reversal-flip
trigger (§8) is owned by Strategy and arrives as a separate signal — Order
Exec handles it via the entry path, not here.

Pure function: takes the position + current premium + current_leaf + clock +
config snapshot. Returns (should_exit, ExitReason). The first matching
trigger wins; remaining triggers ignored on that tick.
"""

from __future__ import annotations

from typing import Any

from state.schemas.position import ExitReason, Position


def evaluate(
    position: Position,
    *,
    current_premium: float,
    current_leaf: dict[str, Any] | None,
    now_ts_ms: int,
    now_hhmm: str,
    daily_loss_circuit_triggered: bool,
    eod_squareoff_hhmm: str = "15:15",
    liquidity_suppress_after_hhmm: str = "15:00",
    spread_skip_pct: float = 0.05,
) -> tuple[bool, ExitReason | None]:
    """Run the 8-trigger cascade. Returns (False, None) if no exit yet."""

    # 1. Daily Loss Circuit
    if daily_loss_circuit_triggered:
        return True, ExitReason.DAILY_LOSS_CIRCUIT

    # 2. EOD Square-Off
    if now_hhmm >= eod_squareoff_hhmm:
        return True, ExitReason.EOD

    # 3. Hard Stop Loss
    if current_premium <= position.sl_level:
        return True, ExitReason.HARD_SL

    # 4. Hard Profit Target
    if current_premium >= position.target_level:
        return True, ExitReason.HARD_TARGET

    # 5. Trailing Stop Loss
    if position.tsl_armed and position.tsl_level is not None and current_premium <= position.tsl_level:
        return True, ExitReason.TRAILING_SL

    # 6. Liquidity exit (suppressed in last 15 min before EOD)
    if now_hhmm < liquidity_suppress_after_hhmm and current_leaf is not None:
        ltp = float(current_leaf.get("ltp") or 0)
        bid = float(current_leaf.get("bid") or 0)
        ask = float(current_leaf.get("ask") or 0)
        if ltp > 0 and bid > 0 and ask > 0 and ask > bid:
            spread_pct = (ask - bid) / ltp
            if spread_pct > spread_skip_pct:
                return True, ExitReason.LIQUIDITY

    # 7. Time exit
    holding_seconds = max(0, (now_ts_ms - int(position.entry_ts.timestamp() * 1000)) // 1000)
    if holding_seconds >= position.exit_profile.max_hold_sec:
        return True, ExitReason.TIME_EXIT

    return False, None


def update_trailing_state(
    position: Position,
    *,
    current_premium: float,
) -> Position:
    """Pure: arm TSL when premium first hits +tsl_arm_pct, then update peak +
    tsl_level on each subsequent higher peak. Returns a copy with mutated
    fields; caller persists the diff to Redis.
    """
    new_peak = max(float(position.peak_premium or 0.0), float(current_premium))

    arm_threshold = float(position.entry_price) * (1.0 + float(position.tsl_arm_pct))
    if not position.tsl_armed and current_premium >= arm_threshold:
        new_armed = True
    else:
        new_armed = position.tsl_armed

    if new_armed:
        new_tsl_level = round(new_peak * (1.0 - float(position.tsl_trail_pct)), 4)
    else:
        new_tsl_level = position.tsl_level

    return position.model_copy(
        update={
            "peak_premium": new_peak,
            "tsl_armed": new_armed,
            "tsl_level": new_tsl_level,
            "current_premium": current_premium,
        }
    )
