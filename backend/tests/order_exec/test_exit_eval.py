"""exit_eval — pure 8-trigger priority cascade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engines.order_exec import exit_eval
from state.schemas.position import ExitProfile, ExitReason, Position


def _position(
    *, entry_price: float = 100.0, qty: int = 75,
    sl_pct: float = 0.20, target_pct: float = 0.50,
    tsl_arm_pct: float = 0.15, tsl_trail_pct: float = 0.05,
    max_hold_sec: int = 1500,
    tsl_armed: bool = False, peak_premium: float | None = None,
    tsl_level: float | None = None,
    entry_age_sec: int = 0,
) -> Position:
    profile = ExitProfile(
        sl_pct=sl_pct, target_pct=target_pct,
        tsl_arm_pct=tsl_arm_pct, tsl_trail_pct=tsl_trail_pct,
        max_hold_sec=max_hold_sec,
    )
    entry_ts = datetime.now(UTC) - timedelta(seconds=entry_age_sec)
    return Position(
        pos_id="P1", sig_id="S1", index="nifty50", side="CE", strike=23000,
        instrument_token="NSE_FO|49520", qty=qty,
        entry_order_id="O1",
        entry_price=entry_price, entry_ts=entry_ts,
        mode="paper", intent="FRESH_ENTRY",
        sl_level=round(entry_price * (1 - sl_pct), 4),
        target_level=round(entry_price * (1 + target_pct), 4),
        tsl_armed=tsl_armed,
        tsl_arm_pct=tsl_arm_pct, tsl_trail_pct=tsl_trail_pct,
        tsl_level=tsl_level,
        peak_premium=peak_premium if peak_premium is not None else entry_price,
        current_premium=entry_price,
        exit_profile=profile,
        sum_ce_at_entry=0.0, sum_pe_at_entry=0.0,
        strategy_version="t",
    )


def test_priority_daily_loss_circuit_first() -> None:
    pos = _position()
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=100.0,
        current_leaf={"ltp": 100, "bid": 99, "ask": 101},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="15:20",  # also EOD
        daily_loss_circuit_triggered=True,  # but circuit wins
    )
    assert should is True and reason == ExitReason.DAILY_LOSS_CIRCUIT


def test_priority_eod_over_sl() -> None:
    pos = _position()
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=70.0,  # below SL=80
        current_leaf={"ltp": 70, "bid": 69, "ask": 71},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="15:15",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.EOD


def test_hard_sl_when_premium_below_threshold() -> None:
    pos = _position()
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=79.0,  # SL=80
        current_leaf={"ltp": 79, "bid": 78, "ask": 80},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.HARD_SL


def test_hard_target() -> None:
    pos = _position()
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=151.0,  # target=150
        current_leaf={"ltp": 151, "bid": 150, "ask": 152},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.HARD_TARGET


def test_trailing_sl_only_when_armed() -> None:
    # Not armed: drop below tsl_level should NOT trigger TSL
    pos = _position(tsl_armed=False, tsl_level=110.0)
    should, _ = exit_eval.evaluate(
        pos,
        current_premium=109.0,
        current_leaf={"ltp": 109, "bid": 108, "ask": 110},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is False

    # Armed + below level → TSL fires
    pos2 = _position(tsl_armed=True, peak_premium=120.0, tsl_level=114.0)
    should, reason = exit_eval.evaluate(
        pos2,
        current_premium=113.0,
        current_leaf={"ltp": 113, "bid": 112, "ask": 114},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.TRAILING_SL


def test_liquidity_exit_during_normal_hours() -> None:
    pos = _position()
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=100.0,
        current_leaf={"ltp": 100, "bid": 90, "ask": 110},  # 20% spread
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.LIQUIDITY


def test_liquidity_exit_suppressed_after_15_00() -> None:
    pos = _position()
    should, _ = exit_eval.evaluate(
        pos,
        current_premium=100.0,
        current_leaf={"ltp": 100, "bid": 90, "ask": 110},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="15:05",
        daily_loss_circuit_triggered=False,
    )
    assert should is False


def test_time_exit_after_max_hold() -> None:
    pos = _position(max_hold_sec=600, entry_age_sec=601)
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=100.0,
        current_leaf={"ltp": 100, "bid": 99, "ask": 101},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is True and reason == ExitReason.TIME_EXIT


def test_no_exit_when_within_bounds() -> None:
    pos = _position(max_hold_sec=1500, entry_age_sec=10)
    should, reason = exit_eval.evaluate(
        pos,
        current_premium=100.0,
        current_leaf={"ltp": 100, "bid": 99.5, "ask": 100.5},
        now_ts_ms=int(datetime.now(UTC).timestamp() * 1000),
        now_hhmm="11:00",
        daily_loss_circuit_triggered=False,
    )
    assert should is False and reason is None


# ── update_trailing_state ────────────────────────────────────────────


def test_update_trailing_state_arms_at_threshold() -> None:
    pos = _position()
    out = exit_eval.update_trailing_state(pos, current_premium=115.0)  # +15% arms
    assert out.tsl_armed is True
    assert out.peak_premium == 115.0
    # tsl_level = 115 * (1 - 0.05) = 109.25
    assert abs((out.tsl_level or 0) - 109.25) < 0.001


def test_update_trailing_state_tracks_peak_after_armed() -> None:
    pos = _position(tsl_armed=True, peak_premium=120.0, tsl_level=114.0)
    out = exit_eval.update_trailing_state(pos, current_premium=125.0)
    assert out.peak_premium == 125.0
    assert abs((out.tsl_level or 0) - 118.75) < 0.001


def test_update_trailing_state_does_not_lower_peak() -> None:
    pos = _position(tsl_armed=True, peak_premium=130.0, tsl_level=123.5)
    out = exit_eval.update_trailing_state(pos, current_premium=120.0)
    assert out.peak_premium == 130.0  # unchanged
    assert abs((out.tsl_level or 0) - 123.5) < 0.001
