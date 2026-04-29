"""Smoke test for the UpstoxAPI facade — exercises the import graph and
spot-checks that the dict-forwarding contract works end-to-end."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox import UpstoxAPI


def test_facade_exposes_method_groups() -> None:
    methods = [
        # auth & user
        "validate_token",
        "request_access_token",
        "get_profile",
        "get_capital",
        "get_kill_switch_status",
        "set_kill_switch",
        "is_segment_blocked",
        "get_static_ips",
        "update_static_ips",
        # market metadata
        "get_holidays",
        "get_holiday_by_date",
        "is_holiday_for",
        "get_market_timings",
        "is_standard_session",
        "get_market_status",
        "is_market_open",
        "is_market_pre_open",
        "get_ltp",
        "download_master_contract",
        "get_historical_candles",
        "get_intraday_candles",
        # options
        "get_option_contracts",
        "expiries_for",
        "nearest_expiry",
        "strikes_for",
        "get_option_chain",
        "total_pcr",
        "strikes_around_atm",
        "get_option_greeks",
        "get_brokerage",
        # orders + positions
        "place_order",
        "modify_order",
        "cancel_order",
        "get_order_status",
        "get_order_history",
        "get_order_book",
        "get_trades_for_day",
        "get_trades_by_order",
        "exit_all_positions",
        "save_api_log",
        "get_positions",
        # streamers
        "market_streamer",
        "build_market_streamer",
        "portfolio_streamer",
        "build_portfolio_streamer",
    ]
    for m in methods:
        assert hasattr(UpstoxAPI, m), f"UpstoxAPI missing {m}"


@respx.mock
def test_facade_get_capital_forwards_params() -> None:
    respx.get("https://api.upstox.com/v3/user/get-funds-and-margin").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"available_to_trade": {"total": 9999.0}}}
        )
    )
    res = UpstoxAPI.get_capital({"access_token": "t"})
    assert res["success"] is True
    assert res["data"]["available_to_trade"]["total"] == 9999.0


def test_facade_predicates_are_pure() -> None:
    assert UpstoxAPI.is_holiday_for([], "NSE") is False
    assert UpstoxAPI.is_segment_blocked(None, "NSE_FO") is True
    assert UpstoxAPI.is_market_open("NORMAL_OPEN") is True
    assert UpstoxAPI.is_market_pre_open("PRE_OPEN_START") is True


@respx.mock
def test_facade_validate_token_passes_through() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {}})
    )
    assert UpstoxAPI.validate_token({"access_token": "x"}) is True
