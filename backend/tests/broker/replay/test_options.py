"""Replay tests for option_contract / option_chain / option_greeks / brokerage."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.brokerage import get_brokerage
from brokers.upstox.option_chain import get_option_chain
from brokers.upstox.option_contract import get_option_contracts
from brokers.upstox.option_greeks import get_option_greeks


@respx.mock
def test_get_option_contracts_happy() -> None:
    respx.get("https://api.upstox.com/v2/option/contract").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "expiry": "2026-05-08",
                        "instrument_type": "CE",
                        "strike_price": 23000,
                        "instrument_key": "NSE_FO|123",
                        "lot_size": 75,
                    }
                ],
            },
        )
    )
    res = get_option_contracts("NSE_INDEX|Nifty 50", access_token="t")
    assert res["success"] is True
    assert res["data"][0]["strike_price"] == 23000


@respx.mock
def test_get_option_contracts_404() -> None:
    respx.get("https://api.upstox.com/v2/option/contract").mock(
        return_value=httpx.Response(404, json={"status": "error"})
    )
    res = get_option_contracts("NSE_INDEX|Bad", access_token="t")
    assert res["success"] is False


@respx.mock
def test_get_option_chain_sorts_by_strike() -> None:
    respx.get("https://api.upstox.com/v2/option/chain").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"strike_price": 23100, "underlying_spot_price": 23000.0},
                    {"strike_price": 22900, "underlying_spot_price": 23000.0},
                    {"strike_price": 23000, "underlying_spot_price": 23000.0},
                ],
            },
        )
    )
    res = get_option_chain("NSE_INDEX|Nifty 50", "2026-05-08", access_token="t")
    assert res["success"] is True
    assert [r["strike_price"] for r in res["data"]] == [22900, 23000, 23100]


@respx.mock
def test_get_option_chain_error() -> None:
    respx.get("https://api.upstox.com/v2/option/chain").mock(
        return_value=httpx.Response(400, json={"status": "error"})
    )
    res = get_option_chain("NSE_INDEX|Nifty 50", "2099-01-01", access_token="t")
    assert res["success"] is False


@respx.mock
def test_get_option_greeks_rekeyed_by_token() -> None:
    respx.get("https://api.upstox.com/v3/market-quote/option-greek").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_FO:NIFTY...23000CE": {
                        "instrument_token": "NSE_FO|49520",
                        "delta": 0.5,
                        "iv": 0.18,
                    }
                },
            },
        )
    )
    res = get_option_greeks(["NSE_FO|49520"], access_token="t")
    assert res["success"] is True
    assert "NSE_FO|49520" in res["data"]
    assert res["data"]["NSE_FO|49520"]["delta"] == 0.5


@respx.mock
def test_get_brokerage_normalizes_net_value() -> None:
    respx.get("https://api.upstox.com/v2/charges/brokerage").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "charges": {
                        "total": 25.5,
                        "brokerage": 20.0,
                        "taxes": {"gst": 3.6, "stt": 1.5, "stamp_duty": 0.4},
                    }
                },
            },
        )
    )
    res = get_brokerage(
        instrument_token="NSE_FO|49520",
        quantity=75,
        product="I",
        transaction_type="BUY",
        price=100.0,
        access_token="t",
    )
    assert res["success"] is True
    assert res["data"]["gross"] == 7500.0
    assert res["data"]["total"] == 25.5
    assert res["data"]["net_value"] == 7474.5
