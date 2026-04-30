"""Replay tests for holidays / market_timings / market_status / market_data."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.holidays import get_holiday_by_date, get_holidays
from brokers.upstox.market_data import get_ltp
from brokers.upstox.market_status import get_market_status
from brokers.upstox.market_timings import get_market_timings


@respx.mock
def test_get_holidays_normalizes() -> None:
    respx.get("https://api.upstox.com/v2/market/holidays").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "date": "2026-08-15",
                        "description": "Independence Day",
                        "holiday_type": "TRADING_HOLIDAY",
                        "closed_exchanges": ["NSE", "BSE"],
                        "open_exchanges": [],
                    }
                ],
            },
        )
    )
    res = get_holidays(access_token="t")
    assert res["success"] is True
    e = res["data"][0]
    assert e["type"] == "TRADING_HOLIDAY"
    assert e["is_fully_closed"] is True
    assert "NSE" in e["closed_exchanges"]


@respx.mock
def test_get_holiday_by_date_empty_list() -> None:
    respx.get("https://api.upstox.com/v2/market/holidays/2026-04-29").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    res = get_holiday_by_date("2026-04-29")
    assert res["success"] is True
    assert res["data"] == []


@respx.mock
def test_get_holidays_error() -> None:
    respx.get("https://api.upstox.com/v2/market/holidays").mock(
        return_value=httpx.Response(500, json={"status": "error"})
    )
    res = get_holidays()
    assert res["success"] is False and res["code"] == 500


@respx.mock
def test_get_market_timings_normalizes_hhmm() -> None:
    # 2026-04-29 09:15 IST in epoch ms
    start_epoch = 1777692600000  # 2026-04-29T09:15+0530 (approx)
    end_epoch = 1777715400000  # +6h 15m
    respx.get("https://api.upstox.com/v2/market/timings/2026-04-29").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [{"exchange": "NSE", "start_time": start_epoch, "end_time": end_epoch}],
            },
        )
    )
    res = get_market_timings("2026-04-29", access_token="t")
    assert res["success"] is True
    entry = res["data"][0]
    assert entry["exchange"] == "NSE"
    # start_hhmm/end_hhmm should be present and parseable
    assert entry["start_hhmm"] is not None
    assert ":" in entry["start_hhmm"]


@respx.mock
def test_get_market_status_happy() -> None:
    respx.get("https://api.upstox.com/v2/market/status/NSE").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "exchange": "NSE",
                    "status": "NORMAL_OPEN",
                    "last_updated": 1777692600000,
                },
            },
        )
    )
    res = get_market_status("NSE", access_token="t")
    assert res["success"] is True
    assert res["data"]["is_open"] is True
    assert res["data"]["status"] == "NORMAL_OPEN"


@respx.mock
def test_get_market_status_error() -> None:
    respx.get("https://api.upstox.com/v2/market/status/NSE").mock(
        return_value=httpx.Response(404, json={"status": "error"})
    )
    res = get_market_status("NSE", access_token="t")
    assert res["success"] is False


@respx.mock
def test_get_ltp_happy_filters_zero_prices() -> None:
    respx.get("https://api.upstox.com/v3/market-quote/ltp").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_INDEX:Nifty 50": {
                        "last_price": 23010.5,
                        "instrument_token": "NSE_INDEX|Nifty 50",
                    },
                    "NSE_FO:NIFTY_CE": {"last_price": 0.0, "instrument_token": "NSE_FO|49520"},
                    "NSE_FO:NIFTY_PE": {"last_price": 142.5, "instrument_token": "NSE_FO|49521"},
                },
            },
        )
    )
    res = get_ltp(["NSE_INDEX|Nifty 50", "NSE_FO|49520", "NSE_FO|49521"], access_token="t")
    assert res["success"] is True
    assert res["data"] == {"NSE_INDEX|Nifty 50": 23010.5, "NSE_FO|49521": 142.5}


@respx.mock
def test_get_ltp_error() -> None:
    respx.get("https://api.upstox.com/v3/market-quote/ltp").mock(
        return_value=httpx.Response(500, json={"status": "error"})
    )
    res = get_ltp(["NSE_INDEX|Nifty 50"], access_token="t")
    assert res["success"] is False
