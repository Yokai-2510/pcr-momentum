"""Dynamic ATM basket management (Strategy.md §3).

The basket is a set of CE strikes + PE strikes around ATM.
ATM = round(spot / strike_step) * strike_step.

A basket shift is triggered when:
    abs(current_spot - last_atm) >= strike_step
AND
    now() - last_shift_ts >= hysteresis_sec   (default 5s, prevents thrash on
                                                 strike-boundary chop)

This module is pure: given inputs (spot, current basket, instrument config,
last_shift_ts) it returns a `BasketTransition` describing what to add/drop.
The runner applies the transition and updates `market_data:subscriptions:desired`
so the data-pipeline picks up the new tokens.

NOTE: token discovery (strike -> instrument_token mapping) is loaded from
`market_data:instruments:master` at vessel boot, then cached vessel-locally.
Strike basket builder lives outside this module (init engine), and is
re-used here: we ask `instruments_master.tokens_for(index, expiry, strikes)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Basket:
    """Vessel-local basket spec. Mutable; runner updates it on each shift."""

    atm: int
    ce_strikes: list[int] = field(default_factory=list)  # sorted
    pe_strikes: list[int] = field(default_factory=list)  # sorted
    ce_tokens: dict[int, str] = field(default_factory=dict)  # strike -> token
    pe_tokens: dict[int, str] = field(default_factory=dict)
    last_shift_ts: int = 0  # ms

    def all_tokens(self) -> list[str]:
        return list(self.ce_tokens.values()) + list(self.pe_tokens.values())

    def ce_pairs(self) -> list[tuple[int, str]]:
        return [(s, self.ce_tokens[s]) for s in self.ce_strikes if s in self.ce_tokens]

    def pe_pairs(self) -> list[tuple[int, str]]:
        return [(s, self.pe_tokens[s]) for s in self.pe_strikes if s in self.pe_tokens]


@dataclass(slots=True, frozen=True)
class BasketTransition:
    new_basket: Basket
    added_tokens: list[str]
    dropped_tokens: list[str]
    reason: str


def compute_atm(spot: float, strike_step: int) -> int:
    return int(round(spot / strike_step) * strike_step)


def compute_strike_set(atm: int, strike_step: int, basket_size: int) -> tuple[list[int], list[int]]:
    """Return (ce_strikes, pe_strikes) — both sorted ascending.

    `basket_size` is the half-width: `basket_size=5` means strikes within
    ATM-5*step .. ATM+5*step inclusive (11 strikes per side).
    """
    span = list(range(-basket_size, basket_size + 1))
    strikes = [atm + i * strike_step for i in span]
    return sorted(strikes), sorted(strikes)


def maybe_shift_basket(
    *,
    current: Basket,
    spot: float | None,
    strike_step: int,
    basket_size: int,
    now_ms: int,
    hysteresis_sec: int = 5,
    token_lookup: Any | None = None,        # callable: (strike, side) -> token | None
) -> BasketTransition | None:
    """Return a transition if a shift is warranted; else None.

    `token_lookup`: a callable that the runner provides to map (strike, "CE"/"PE")
    to an instrument_token, sourced from `market_data:instruments:master`.
    """
    if spot is None:
        return None
    new_atm = compute_atm(spot, strike_step)
    if new_atm == current.atm and current.ce_tokens:
        return None  # No shift needed and basket already populated
    if current.last_shift_ts and (now_ms - current.last_shift_ts) < hysteresis_sec * 1000:
        return None  # Hysteresis still active

    new_ce, new_pe = compute_strike_set(new_atm, strike_step, basket_size)

    new_basket = Basket(atm=new_atm, ce_strikes=new_ce, pe_strikes=new_pe, last_shift_ts=now_ms)

    if token_lookup is not None:
        for s in new_ce:
            tok = token_lookup(s, "CE")
            if tok:
                new_basket.ce_tokens[s] = tok
        for s in new_pe:
            tok = token_lookup(s, "PE")
            if tok:
                new_basket.pe_tokens[s] = tok

    old_tokens = set(current.all_tokens())
    new_tokens = set(new_basket.all_tokens())
    added = sorted(new_tokens - old_tokens)
    dropped = sorted(old_tokens - new_tokens)

    reason = (
        "initial_build"
        if not current.ce_tokens
        else f"atm_shift_{current.atm}->{new_atm}"
    )

    return BasketTransition(
        new_basket=new_basket,
        added_tokens=added,
        dropped_tokens=dropped,
        reason=reason,
    )
