"""
engines.strategy.pipeline - pre-signal gates.

Strategy.md sections 11 and 10.1 split system gates between Strategy
(entry-time) and
Order Exec (pre-place-time). Strategy enforces only the cheap, system-wide
gates here - the order-exec stage runs the full broker-spread + depth +
circuit-limit cascade.

All functions are pure-ish: they take in dicts and return (bool, reason).
"""

from __future__ import annotations

from typing import Any


def system_gates_pass(redis_snapshot: dict[str, Any]) -> tuple[bool, str]:
    """Cheap system-flag check. Reads pre-fetched values.

    Args:
        redis_snapshot: dict with at least:
          {trading_active, daily_loss_circuit_triggered, ready,
           kill_switch_engaged_nse_fo}

    Returns:
        (True, "ok") if all gates pass.
        (False, "<reason>") on first failure (short-circuited).
    """
    if redis_snapshot.get("ready") != "true":
        return False, "init_not_ready"
    if redis_snapshot.get("trading_active") != "true":
        return False, "trading_inactive"
    if redis_snapshot.get("daily_loss_circuit_triggered") == "true":
        return False, "daily_loss_circuit"
    if redis_snapshot.get("kill_switch_engaged_nse_fo") is True:
        return False, "kill_switch_engaged"
    return True, "ok"


def liquidity_gate_pass(
    leaf: dict[str, Any],
    intended_lots: int,
    lot_size: int,
    spread_skip_pct: float,
) -> tuple[bool, str]:
    """Per-strike liquidity check before emitting a signal on this strike.

    Cheap version (Strategy-side):
      - LTP > 0
      - bid > 0 and ask > 0
      - (ask - bid) / ltp <= spread_skip_pct
      - ask_qty >= intended_lots * lot_size  (depth)

    The full broker-side spread + circuit + best-bid-price-walk check happens
    in Order Exec (Strategy.md section 10.1).
    """
    ltp = float(leaf.get("ltp") or 0)
    bid = float(leaf.get("bid") or 0)
    ask = float(leaf.get("ask") or 0)
    ask_qty = int(leaf.get("ask_qty") or 0)

    if ltp <= 0:
        return False, "no_ltp"
    if bid <= 0 or ask <= 0:
        return False, "no_quotes"
    if ask <= bid:
        return False, "crossed_book"
    if (ask - bid) / ltp > spread_skip_pct:
        return False, f"spread_too_wide:{(ask - bid) / ltp:.4f}"
    needed = intended_lots * lot_size
    if ask_qty > 0 and ask_qty < needed:
        return False, f"thin_depth:{ask_qty}<{needed}"
    return True, "ok"


def in_entry_freeze(now_hhmm: str, freeze_hhmm: str = "15:10") -> bool:
    """True iff `now_hhmm` >= `freeze_hhmm` (string compare on HH:MM works)."""
    return now_hhmm >= freeze_hhmm


def at_daily_caps(
    entries_today: int,
    reversals_today: int,
    intent: str,
    max_entries: int,
    max_reversals: int,
) -> tuple[bool, str]:
    """True if the relevant cap is reached for the proposed `intent`.

    intent in {FRESH_ENTRY, REVERSAL_FLIP}.
    """
    if entries_today >= max_entries:
        return True, f"entry_cap_reached:{entries_today}>={max_entries}"
    if intent == "REVERSAL_FLIP" and reversals_today >= max_reversals:
        return True, f"reversal_cap_reached:{reversals_today}>={max_reversals}"
    return False, "ok"
