"""Pure premium-diff helpers."""

from __future__ import annotations

from engines.strategy.premium_diff import (
    all_strikes_negative,
    compute_diffs,
    compute_sums,
    pick_highest_diff_strike,
)


def test_compute_diffs_basic() -> None:
    cur = {"CE_22950": 110.0, "CE_23000": 100.0, "PE_23050": 80.0}
    pre = {"CE_22950": 100.0, "CE_23000": 100.0, "PE_23050": 90.0}
    assert compute_diffs(cur, pre) == {"CE_22950": 10.0, "CE_23000": 0.0, "PE_23050": -10.0}


def test_compute_diffs_drops_unknown_tokens() -> None:
    cur = {"CE_X": 50.0}
    pre = {"CE_Y": 100.0}
    assert compute_diffs(cur, pre) == {}


def test_compute_diffs_drops_none_values() -> None:
    cur = {"CE_22950": None, "CE_23000": 100.0}  # type: ignore[dict-item]
    pre = {"CE_22950": 100.0, "CE_23000": 90.0}
    assert compute_diffs(cur, pre) == {"CE_23000": 10.0}


def test_compute_sums_basic() -> None:
    diffs = {"CE_A": 5.0, "CE_B": -3.0, "PE_X": 2.0, "PE_Y": 4.0}
    sum_ce, sum_pe = compute_sums(diffs, ["CE_A", "CE_B"], ["PE_X", "PE_Y"])
    assert sum_ce == 2.0
    assert sum_pe == 6.0


def test_compute_sums_missing_token_contributes_zero() -> None:
    diffs = {"CE_A": 5.0}
    sum_ce, sum_pe = compute_sums(diffs, ["CE_A", "CE_MISSING"], ["PE_MISSING"])
    assert sum_ce == 5.0
    assert sum_pe == 0.0


def test_pick_highest_diff_picks_max_abs() -> None:
    diffs = {"A": 3.0, "B": -10.0, "C": 7.0}
    tok, d = pick_highest_diff_strike(diffs, ["A", "B", "C"])
    assert tok == "B"
    assert d == -10.0


def test_pick_highest_diff_empty_returns_none() -> None:
    tok, d = pick_highest_diff_strike({}, [])
    assert tok is None
    assert d == 0.0


def test_pick_highest_diff_all_zero_returns_none() -> None:
    tok, _ = pick_highest_diff_strike({"A": 0.0, "B": 0.0}, ["A", "B"])
    assert tok is None


def test_all_strikes_negative_true() -> None:
    diffs = {"A": -1.0, "B": -5.0}
    assert all_strikes_negative(diffs, ["A", "B"]) is True


def test_all_strikes_negative_one_positive() -> None:
    diffs = {"A": -1.0, "B": 5.0}
    assert all_strikes_negative(diffs, ["A", "B"]) is False


def test_all_strikes_negative_empty_token_list_false() -> None:
    assert all_strikes_negative({}, []) is False


def test_all_strikes_negative_zero_is_negative() -> None:
    # Zero counts as "not positive", so all-negative is True.
    assert all_strikes_negative({"A": 0.0, "B": -1.0}, ["A", "B"]) is True
