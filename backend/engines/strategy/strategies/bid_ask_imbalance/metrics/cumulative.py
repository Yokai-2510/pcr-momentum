"""Cumulative CE / PE imbalance (Strategy.md §4.6).

    cum_ce_imbalance = Σ(CE bid qty across all CE strikes) / Σ(CE ask qty ...)
    cum_pe_imbalance = Σ(PE bid qty across all PE strikes) / Σ(PE ask qty ...)

Smooths single-strike noise. Returns None if either side has insufficient
data (no qtys at all on that side).
"""

from __future__ import annotations

from engines.strategy.strategies.bid_ask_imbalance.snapshot import StrikeLeg


def _sum_or_none(values: list[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def cumulative_imbalance(legs: tuple[StrikeLeg, ...]) -> tuple[int | None, int | None, float | None]:
    """Return (Σbid, Σask, ratio) across the given legs.

    Ratio is None if Σask is None or 0.
    """
    sum_bid = _sum_or_none([leg.total_bid_qty for leg in legs])
    sum_ask = _sum_or_none([leg.total_ask_qty for leg in legs])

    if sum_bid is None or sum_ask is None or sum_ask <= 0:
        return sum_bid, sum_ask, None
    return sum_bid, sum_ask, sum_bid / sum_ask
