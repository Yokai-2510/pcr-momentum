"""Decision tree per Strategy.md sections 6 and 8."""

from __future__ import annotations

from engines.strategy.decision import (
    decide_when_cooldown,
    decide_when_flat,
    decide_when_in_ce,
    decide_when_in_pe,
)

REV = 20.0  # reversal_threshold_inr (NIFTY default)
DOM = 20.0  # entry_dominance_threshold_inr (default = REV)


# decide_when_flat


def test_flat_ce_only_positive() -> None:
    assert (
        decide_when_flat(
            sum_ce=10, sum_pe=-5, delta=-15, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "BUY_CE"
    )


def test_flat_pe_only_positive() -> None:
    assert (
        decide_when_flat(
            sum_ce=-5, sum_pe=10, delta=15, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "BUY_PE"
    )


def test_flat_both_positive_dominant_pe() -> None:
    # delta = 30 > 20: BUY_PE (PE leading by gap)
    assert (
        decide_when_flat(
            sum_ce=10, sum_pe=40, delta=30, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "BUY_PE"
    )


def test_flat_both_positive_dominant_ce() -> None:
    # delta = -30: BUY_CE (CE leading by gap; SUM_CE > SUM_PE)
    assert (
        decide_when_flat(
            sum_ce=40, sum_pe=10, delta=-30, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "BUY_CE"
    )


def test_flat_both_positive_ambiguous_wait() -> None:
    # delta = 10 <= 20: WAIT (ambiguous)
    assert (
        decide_when_flat(
            sum_ce=20, sum_pe=30, delta=10, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "WAIT"
    )


def test_flat_both_negative_wait_recovery() -> None:
    assert (
        decide_when_flat(
            sum_ce=-5, sum_pe=-3, delta=2, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "WAIT_RECOVERY"
    )


def test_flat_both_negative_ce_crosses_threshold_buys_ce() -> None:
    assert (
        decide_when_flat(
            sum_ce=25, sum_pe=-5, delta=-30, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "BUY_CE"
    )


def test_flat_zero_zero_wait_recovery() -> None:
    assert (
        decide_when_flat(
            sum_ce=0, sum_pe=0, delta=0, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "WAIT_RECOVERY"
    )


def test_flat_one_zero_one_negative_wait_recovery() -> None:
    # SUM_CE=0 (<=0), SUM_PE=-5 (<=0): both <=0 path -> WAIT_RECOVERY
    assert (
        decide_when_flat(
            sum_ce=0, sum_pe=-5, delta=-5, reversal_threshold=REV, dominance_threshold=DOM
        )
        == "WAIT_RECOVERY"
    )


# decide_when_in_ce


def test_in_ce_flip_above_threshold() -> None:
    assert decide_when_in_ce(delta=25, threshold=20) == "FLIP_TO_PE"


def test_in_ce_hold_at_threshold_boundary() -> None:
    # strict > so equals is HOLD
    assert decide_when_in_ce(delta=20, threshold=20) == "HOLD"


def test_in_ce_hold_negative_delta() -> None:
    assert decide_when_in_ce(delta=-100, threshold=20) == "HOLD"


# decide_when_in_pe


def test_in_pe_flip_below_negative_threshold() -> None:
    assert decide_when_in_pe(delta=-25, threshold=20) == "FLIP_TO_CE"


def test_in_pe_hold_at_negative_threshold_boundary() -> None:
    assert decide_when_in_pe(delta=-20, threshold=20) == "HOLD"


def test_in_pe_hold_positive_delta() -> None:
    assert decide_when_in_pe(delta=100, threshold=20) == "HOLD"


# decide_when_cooldown


def test_cooldown_still_active() -> None:
    assert decide_when_cooldown(now_ts_ms=999, cooldown_until_ts_ms=1000) == "CONTINUE_WAIT"


def test_cooldown_at_boundary_clears() -> None:
    assert decide_when_cooldown(now_ts_ms=1000, cooldown_until_ts_ms=1000) == "GO_FLAT"


def test_cooldown_past_clears() -> None:
    assert decide_when_cooldown(now_ts_ms=2000, cooldown_until_ts_ms=1000) == "GO_FLAT"
