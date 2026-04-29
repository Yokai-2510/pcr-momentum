"""Pure predicates with no I/O."""

from __future__ import annotations

from brokers.upstox.holidays import is_holiday_for
from brokers.upstox.kill_switch import is_segment_blocked
from brokers.upstox.market_status import is_open, is_pre_open
from brokers.upstox.market_timings import is_standard_session

# ── is_holiday_for ────────────────────────────────────────────────────


def test_holidays_empty_is_not_holiday() -> None:
    assert is_holiday_for([], "NSE") is False
    assert is_holiday_for(None, "NSE") is False


def test_holidays_closed_exchange_match() -> None:
    entries = [{"closed_exchanges": ["NSE", "BSE"], "open_exchanges": []}]
    assert is_holiday_for(entries, "NSE") is True


def test_holidays_fully_closed_means_holiday() -> None:
    entries = [{"closed_exchanges": [], "open_exchanges": []}]
    assert is_holiday_for(entries, "NSE") is True


def test_holidays_exchange_not_in_open_list_means_holiday() -> None:
    entries = [{"closed_exchanges": [], "open_exchanges": [{"exchange": "BSE"}]}]
    assert is_holiday_for(entries, "NSE") is True


def test_holidays_exchange_in_open_list_is_not_holiday() -> None:
    entries = [{"closed_exchanges": [], "open_exchanges": [{"exchange": "NSE"}]}]
    assert is_holiday_for(entries, "NSE") is False


# ── kill_switch.is_segment_blocked ────────────────────────────────────


def test_kill_switch_no_snapshot_blocks() -> None:
    assert is_segment_blocked(None, "NSE_FO") is True
    assert is_segment_blocked([], "NSE_FO") is True


def test_kill_switch_unknown_segment_blocks() -> None:
    snap = [{"segment": "NSE_EQ", "segment_status": "ACTIVE", "kill_switch_enabled": False}]
    assert is_segment_blocked(snap, "NSE_FO") is True


def test_kill_switch_inactive_blocks() -> None:
    snap = [{"segment": "NSE_FO", "segment_status": "INACTIVE", "kill_switch_enabled": False}]
    assert is_segment_blocked(snap, "NSE_FO") is True


def test_kill_switch_engaged_blocks() -> None:
    snap = [{"segment": "NSE_FO", "segment_status": "ACTIVE", "kill_switch_enabled": True}]
    assert is_segment_blocked(snap, "NSE_FO") is True


def test_kill_switch_active_off_passes() -> None:
    snap = [{"segment": "NSE_FO", "segment_status": "ACTIVE", "kill_switch_enabled": False}]
    assert is_segment_blocked(snap, "NSE_FO") is False


# ── market_status.is_open / is_pre_open ───────────────────────────────


def test_market_open_predicates() -> None:
    assert is_open("NORMAL_OPEN") is True
    assert is_open("SPECIAL_OPEN") is True
    assert is_open("PRE_OPEN_START") is False
    assert is_open(None) is False
    assert is_pre_open("PRE_OPEN_START") is True
    assert is_pre_open("PRE_OPEN_END") is True
    assert is_pre_open("NORMAL_OPEN") is False


# ── market_timings.is_standard_session ────────────────────────────────


def test_is_standard_session_match() -> None:
    entries = [
        {"exchange": "NSE", "start_hhmm": "09:15", "end_hhmm": "15:30"},
    ]
    assert is_standard_session(entries, "NSE") is True


def test_is_standard_session_special_timing() -> None:
    entries = [{"exchange": "NSE", "start_hhmm": "18:00", "end_hhmm": "19:00"}]
    assert is_standard_session(entries, "NSE") is False


def test_is_standard_session_missing_exchange() -> None:
    entries = [{"exchange": "BSE", "start_hhmm": "09:15", "end_hhmm": "15:30"}]
    assert is_standard_session(entries, "NSE") is False
    assert is_standard_session(None, "NSE") is False
    assert is_standard_session([], "NSE") is False
