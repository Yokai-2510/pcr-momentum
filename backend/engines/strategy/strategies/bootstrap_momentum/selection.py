"""Option selection + option-level filters. Faithful port of the original
rank-momentum `instrument_selection` + `entry_filters.check_option_filters`.

Strike selection:
    strike_reference (ATM / OTM / ITM) + strike_offset resolve to a position
    in the ascending strike list, symmetric across CE/PE via a direction
    multiplier (CE OTM = higher strikes, PE OTM = lower strikes; inverse for
    ITM). offset semantics: 0 = the ATM strike (ATM bucket) or the first
    ITM/OTM strike (ITM/OTM buckets); N = N strikes further in that direction.
    Out-of-bounds clamps back to ATM (original behavior).

Filters:
    premium range — reject options whose LTP is outside [min_ltp, max_ltp]
    (too cheap = gamma noise / thin book; too expensive = wide spread, high
    per-lot risk).
"""

from __future__ import annotations

from typing import Any

from engines.strategy.strategies.bootstrap_momentum.direction import (
    Chain,
    Leaf,
    find_atm_index,
)


def _apply_offset(
    atm_idx: int, n_strikes: int, moneyness: str, offset: int, option_type: str
) -> int:
    otm_dir = 1 if option_type.upper() == "CE" else -1
    m = moneyness.upper()
    if m == "OTM":
        idx = atm_idx + otm_dir * (1 + offset)
    elif m == "ITM":
        idx = atm_idx - otm_dir * (1 + offset)
    else:  # ATM: offset moves outward (toward OTM)
        idx = atm_idx + otm_dir * offset
    if idx < 0 or idx >= n_strikes:
        idx = atm_idx  # clamp back to ATM on out-of-bounds (original behavior)
    return idx


def validate_leaf(leaf: Leaf) -> tuple[bool, str]:
    """Option must have a live LTP and a broker instrument token."""
    if float(leaf.get("ltp") or 0.0) <= 0:
        return False, "OPTION_LTP_ZERO"
    if not leaf.get("token"):
        return False, "NO_INSTRUMENT_KEY"
    return True, ""


def select_strike(
    chain: Chain,
    *,
    spot: float,
    moneyness: str,
    offset: int,
    option_type: str,
) -> tuple[Leaf | None, int, str]:
    """Pick the strike per moneyness+offset. Returns (leaf|None, strike, reason)."""
    if not chain:
        return None, 0, "NO_OPTION_CHAIN"
    strikes = sorted(chain.keys())
    if not strikes:
        return None, 0, "NO_STRIKES"

    atm_idx = find_atm_index(strikes, spot)
    target_idx = _apply_offset(atm_idx, len(strikes), moneyness, offset, option_type)
    strike = strikes[target_idx]
    leaf = chain.get(strike)
    if leaf is None:
        return None, strike, "NO_OPTION_DATA"

    ok, reason = validate_leaf(leaf)
    if not ok:
        return None, strike, reason
    return leaf, strike, ""


def premium_filter(leaf: Leaf, premium_cfg: dict[str, Any]) -> tuple[bool, str]:
    """Reject options outside the configured LTP range."""
    if not premium_cfg.get("enabled", False):
        return True, ""
    ltp = float(leaf.get("ltp") or 0.0)
    min_ltp = float(premium_cfg.get("min_ltp", 0.0))
    max_ltp = float(premium_cfg.get("max_ltp", float("inf")))
    if ltp < min_ltp:
        return False, f"PREMIUM_TOO_LOW:{ltp:.2f}<{min_ltp}"
    if ltp > max_ltp:
        return False, f"PREMIUM_TOO_HIGH:{ltp:.2f}>{max_ltp}"
    return True, ""
