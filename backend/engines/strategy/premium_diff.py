"""
engines.strategy.premium_diff - pure helpers.

Per Strategy.md sections 5 and 6:

  diff[token]  = current_premium[token] - pre_open_premium[token]
  SUM_CE       = sum over CE basket strikes of diff[token]
  SUM_PE       = sum over PE basket strikes of diff[token]
  delta        = SUM_PE - SUM_CE          (signed flip indicator)

Strike-pick within a basket: the strike with the highest individual
absolute-rupee diff (Strategy.md section 6.3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def compute_diffs(
    current: Mapping[str, float | None],
    pre_open: Mapping[str, float],
) -> dict[str, float]:
    """Per-token rupee diff (current LTP minus pre-open baseline LTP).

    Tokens absent from `current` are dropped (we cannot evaluate them this
    tick). Tokens absent from `pre_open` are dropped too - that should never
    happen if pre-open snapshot succeeded, but fail-safe just in case.
    """
    out: dict[str, float] = {}
    for tok, cur in current.items():
        if tok not in pre_open:
            continue
        if cur is None:
            continue
        out[tok] = float(cur) - float(pre_open[tok])
    return out


def compute_sums(
    diffs: dict[str, float],
    ce_tokens: Iterable[str],
    pe_tokens: Iterable[str],
) -> tuple[float, float]:
    """SUM_CE, SUM_PE over the given basket-token lists.

    Missing tokens contribute 0 (treated as no information that tick).
    """
    sum_ce = sum(diffs.get(t, 0.0) for t in ce_tokens)
    sum_pe = sum(diffs.get(t, 0.0) for t in pe_tokens)
    return float(sum_ce), float(sum_pe)


def pick_highest_diff_strike(
    diffs: dict[str, float],
    tokens: Iterable[str],
) -> tuple[str | None, float]:
    """Return (token, diff) of the strike with the largest |diff| within `tokens`.

    Strategy.md section 6.3 picks the highest individual Diff in absolute rupee terms.
    Returns (None, 0.0) when no token has a non-zero diff (or `tokens` empty).
    """
    best_token: str | None = None
    best_diff: float = 0.0
    for t in tokens:
        d = diffs.get(t, 0.0)
        if abs(d) > abs(best_diff):
            best_token = t
            best_diff = d
    return best_token, best_diff


def all_strikes_negative(
    diffs: dict[str, float],
    tokens: Iterable[str],
) -> bool:
    """True iff every token in `tokens` has diff <= 0 (with at least one entry)."""
    seen = False
    for t in tokens:
        seen = True
        if diffs.get(t, 0.0) > 0:
            return False
    return seen
