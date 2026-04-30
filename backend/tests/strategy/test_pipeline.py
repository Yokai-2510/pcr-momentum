"""Pre-signal gate predicates."""

from __future__ import annotations

from engines.strategy.pipeline import (
    at_daily_caps,
    in_entry_freeze,
    liquidity_gate_pass,
    system_gates_pass,
)


def test_system_gates_all_ok() -> None:
    snap = {
        "ready": "true",
        "trading_active": "true",
        "daily_loss_circuit_triggered": "false",
        "kill_switch_engaged_nse_fo": False,
    }
    ok, reason = system_gates_pass(snap)
    assert ok is True and reason == "ok"


def test_system_gates_init_not_ready() -> None:
    snap = {"ready": "false", "trading_active": "true"}
    ok, reason = system_gates_pass(snap)
    assert ok is False and reason == "init_not_ready"


def test_system_gates_trading_inactive() -> None:
    snap = {"ready": "true", "trading_active": "false"}
    ok, reason = system_gates_pass(snap)
    assert ok is False and reason == "trading_inactive"


def test_system_gates_daily_loss_circuit() -> None:
    snap = {"ready": "true", "trading_active": "true", "daily_loss_circuit_triggered": "true"}
    ok, reason = system_gates_pass(snap)
    assert ok is False and reason == "daily_loss_circuit"


def test_system_gates_kill_switch() -> None:
    snap = {
        "ready": "true",
        "trading_active": "true",
        "daily_loss_circuit_triggered": "false",
        "kill_switch_engaged_nse_fo": True,
    }
    ok, reason = system_gates_pass(snap)
    assert ok is False and reason == "kill_switch_engaged"


def _good_leaf() -> dict:
    return {"ltp": 100.0, "bid": 99.5, "ask": 100.5, "ask_qty": 1500}


def test_liquidity_gate_ok() -> None:
    ok, _ = liquidity_gate_pass(_good_leaf(), intended_lots=1, lot_size=75, spread_skip_pct=0.05)
    assert ok is True


def test_liquidity_gate_no_ltp() -> None:
    leaf = _good_leaf() | {"ltp": 0}
    ok, reason = liquidity_gate_pass(leaf, 1, 75, 0.05)
    assert ok is False and reason == "no_ltp"


def test_liquidity_gate_no_quotes() -> None:
    leaf = _good_leaf() | {"bid": 0}
    ok, reason = liquidity_gate_pass(leaf, 1, 75, 0.05)
    assert ok is False and reason == "no_quotes"


def test_liquidity_gate_crossed_book() -> None:
    leaf = {"ltp": 100, "bid": 101, "ask": 99, "ask_qty": 1500}
    ok, reason = liquidity_gate_pass(leaf, 1, 75, 0.05)
    assert ok is False and reason == "crossed_book"


def test_liquidity_gate_wide_spread() -> None:
    leaf = {"ltp": 100, "bid": 90, "ask": 110, "ask_qty": 1500}  # 20% spread
    ok, reason = liquidity_gate_pass(leaf, 1, 75, 0.05)
    assert ok is False and reason.startswith("spread_too_wide")


def test_liquidity_gate_thin_depth() -> None:
    leaf = _good_leaf() | {"ask_qty": 50}  # below 1 * 75
    ok, reason = liquidity_gate_pass(leaf, 1, 75, 0.05)
    assert ok is False and reason.startswith("thin_depth")


def test_in_entry_freeze() -> None:
    assert in_entry_freeze("15:10") is True
    assert in_entry_freeze("15:09") is False
    assert in_entry_freeze("15:14") is True


def test_at_daily_caps_under_entry_cap() -> None:
    capped, _ = at_daily_caps(7, 0, "FRESH_ENTRY", 8, 4)
    assert capped is False


def test_at_daily_caps_at_entry_cap() -> None:
    capped, reason = at_daily_caps(8, 0, "FRESH_ENTRY", 8, 4)
    assert capped is True and "entry_cap" in reason


def test_at_daily_caps_at_reversal_cap() -> None:
    capped, reason = at_daily_caps(2, 4, "REVERSAL_FLIP", 8, 4)
    assert capped is True and "reversal_cap" in reason


def test_at_daily_caps_reversal_under_entries() -> None:
    capped, _ = at_daily_caps(2, 3, "REVERSAL_FLIP", 8, 4)
    assert capped is False
