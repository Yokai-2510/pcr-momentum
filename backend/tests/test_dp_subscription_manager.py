"""data_pipeline.subscription_manager — pure-helper tests."""

from __future__ import annotations

from engines.data_pipeline.subscription_manager import (
    compute_atm,
    compute_desired_set,
    diff_sets,
)


def test_compute_atm_rounds_nearest() -> None:
    assert compute_atm(22987.0, 50) == 23000
    assert compute_atm(22974.0, 50) == 22950
    assert compute_atm(22975.0, 50) == 23000  # banker's rounding boundary


def test_compute_atm_zero_step() -> None:
    assert compute_atm(22987.0, 0) == 0


def test_diff_sets_basic() -> None:
    cur = {"a", "b", "c"}
    des = {"b", "c", "d"}
    to_unsub, to_sub = diff_sets(cur, des)
    assert to_unsub == {"a"}
    assert to_sub == {"d"}


def test_diff_sets_no_change() -> None:
    s = {"a", "b"}
    to_unsub, to_sub = diff_sets(s, s)
    assert to_unsub == set()
    assert to_sub == set()


def test_diff_sets_full_replace() -> None:
    to_unsub, to_sub = diff_sets({"a", "b"}, {"x", "y"})
    assert to_unsub == {"a", "b"}
    assert to_sub == {"x", "y"}


def test_compute_desired_set_includes_spot() -> None:
    spot_per_index = {"nifty50": 23000.0}
    cfgs = {"nifty50": {"spot_token": "NSE_INDEX|Nifty 50"}}
    chain_per_index = {
        "nifty50": {
            "23000": {
                "ce": {"token": "NSE_FO|23000_CE"},
                "pe": {"token": "NSE_FO|23000_PE"},
            }
        }
    }
    desired = compute_desired_set(spot_per_index, cfgs, chain_per_index)
    assert "NSE_INDEX|Nifty 50" in desired
    assert "NSE_FO|23000_CE" in desired
    assert "NSE_FO|23000_PE" in desired


def test_compute_desired_set_skips_missing_tokens() -> None:
    spot_per_index = {"nifty50": 23000.0}
    cfgs = {"nifty50": {"spot_token": "NSE_INDEX|Nifty 50"}}
    chain_per_index = {
        "nifty50": {
            "23000": {"ce": {"token": "NSE_FO|A"}, "pe": None},
            "23050": {"ce": None, "pe": {}},  # empty leaf
        }
    }
    desired = compute_desired_set(spot_per_index, cfgs, chain_per_index)
    assert desired == {"NSE_INDEX|Nifty 50", "NSE_FO|A"}


def test_compute_desired_set_empty_spot_skips_index() -> None:
    spot_per_index = {"nifty50": 0.0}
    cfgs = {"nifty50": {"spot_token": "NSE_INDEX|Nifty 50"}}
    chain_per_index = {"nifty50": {"23000": {"ce": {"token": "X"}, "pe": {"token": "Y"}}}}
    desired = compute_desired_set(spot_per_index, cfgs, chain_per_index)
    # Spot still added (cfg present), but option tokens skipped because spot==0.
    assert desired == {"NSE_INDEX|Nifty 50"}
