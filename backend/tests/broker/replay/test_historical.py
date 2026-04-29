"""Replay tests for historical_candles + intraday_candles."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.historical_candles import (
    get_historical_candles,
    get_intraday_candles,
)


@respx.mock
def test_historical_candles_with_range_url() -> None:
    url = (
        "https://api.upstox.com/v3/historical-candle/"
        "NSE_INDEX|Nifty 50/days/1/2026-04-29/2026-04-22"
    )
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-04-22T00:00:00+05:30", 23000.0, 23150.0, 22950.0, 23100.0, 1000, 0],
                        ["2026-04-23T00:00:00+05:30", 23100.0, 23250.0, 23050.0, 23200.0, 1200, 0],
                    ]
                },
            },
        )
    )
    res = get_historical_candles(
        instrument_key="NSE_INDEX|Nifty 50",
        unit="days",
        interval=1,
        to_date="2026-04-29",
        from_date="2026-04-22",
        access_token="t",
    )
    assert res["success"] is True
    rows = res["data"]["rows"]
    assert len(rows) == 2
    assert rows[0]["open"] == 23000.0 and rows[0]["close"] == 23100.0


@respx.mock
def test_historical_candles_no_from_date_uses_short_url() -> None:
    url = "https://api.upstox.com/v3/historical-candle/NSE_INDEX|Nifty 50/days/1/2026-04-29"
    respx.get(url).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"candles": []}})
    )
    res = get_historical_candles(
        instrument_key="NSE_INDEX|Nifty 50",
        unit="days",
        interval=1,
        to_date="2026-04-29",
        access_token="t",
    )
    assert res["success"] is True
    assert res["data"]["rows"] == []


@respx.mock
def test_intraday_candles_happy() -> None:
    url = "https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty 50/minutes/1"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-04-29T09:15:00+05:30", 23000.0, 23010.0, 22995.0, 23005.0, 50, 0]
                    ]
                },
            },
        )
    )
    res = get_intraday_candles(
        instrument_key="NSE_INDEX|Nifty 50",
        unit="minutes",
        interval=1,
        access_token="t",
    )
    assert res["success"] is True
    assert len(res["data"]["rows"]) == 1


@respx.mock
def test_historical_candles_4xx() -> None:
    url = "https://api.upstox.com/v3/historical-candle/NSE_INDEX|Nifty 50/days/1/2026-04-29"
    respx.get(url).mock(return_value=httpx.Response(400, json={"status": "error"}))
    res = get_historical_candles(
        instrument_key="NSE_INDEX|Nifty 50",
        unit="days",
        interval=1,
        to_date="2026-04-29",
        access_token="t",
    )
    assert res["success"] is False
