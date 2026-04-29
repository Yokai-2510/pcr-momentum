"""Argument-validation rejections that never make a network call."""

from __future__ import annotations

from brokers.upstox.brokerage import get_brokerage
from brokers.upstox.historical_candles import get_historical_candles, get_intraday_candles
from brokers.upstox.market_data import get_ltp
from brokers.upstox.option_greeks import get_option_greeks
from brokers.upstox.orders import (
    cancel_order,
    modify_order,
    place_order,
)


def test_place_order_rejects_zero_quantity() -> None:
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=0,
        transaction_type="BUY",
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_quantity" in res["error"]


def test_place_order_rejects_bad_transaction_type() -> None:
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=1,
        transaction_type="HOLD",
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_transaction_type" in res["error"]


def test_place_order_rejects_bad_order_type() -> None:
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=1,
        transaction_type="BUY",
        order_type="GTT",
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_order_type" in res["error"]


def test_place_order_rejects_bad_product() -> None:
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=1,
        transaction_type="BUY",
        product="X",
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_product" in res["error"]


def test_place_order_rejects_bad_validity() -> None:
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=1,
        transaction_type="BUY",
        validity="GFD",
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_validity" in res["error"]


def test_modify_order_rejects_bad_order_type() -> None:
    res = modify_order(order_id="o1", access_token="t", order_type="GTT")
    assert res["success"] is False
    assert "invalid_order_type" in res["error"]


def test_modify_order_rejects_bad_validity() -> None:
    res = modify_order(order_id="o1", access_token="t", validity="WK")
    assert res["success"] is False
    assert "invalid_validity" in res["error"]


def test_cancel_order_smoke_validation_passes() -> None:
    # cancel_order has no arg validation; this only ensures import works
    assert callable(cancel_order)


def test_brokerage_rejects_zero_qty_or_price() -> None:
    res = get_brokerage(
        instrument_token="NSE_FO|49520",
        quantity=0,
        product="I",
        transaction_type="BUY",
        price=10.0,
        access_token="t",
    )
    assert res["success"] is False
    assert "invalid_qty_or_price" in res["error"]
    res2 = get_brokerage(
        instrument_token="NSE_FO|49520",
        quantity=1,
        product="I",
        transaction_type="BUY",
        price=0.0,
        access_token="t",
    )
    assert res2["success"] is False


def test_brokerage_rejects_bad_product_and_side() -> None:
    res = get_brokerage(
        instrument_token="NSE_FO|49520",
        quantity=1,
        product="XYZ",
        transaction_type="BUY",
        price=1.0,
        access_token="t",
    )
    assert res["success"] is False and "invalid_product" in res["error"]
    res2 = get_brokerage(
        instrument_token="NSE_FO|49520",
        quantity=1,
        product="I",
        transaction_type="HOLD",
        price=1.0,
        access_token="t",
    )
    assert res2["success"] is False and "invalid_transaction_type" in res2["error"]


def test_market_data_empty_keys_returns_empty_envelope() -> None:
    res = get_ltp(instrument_keys=[], access_token="t")
    assert res["success"] is True and res["data"] == {}


def test_option_greeks_too_many_keys_rejected() -> None:
    keys = [f"NSE_FO|{i}" for i in range(51)]
    res = get_option_greeks(instrument_keys=keys, access_token="t")
    assert res["success"] is False and "too_many_keys" in res["error"]


def test_option_greeks_empty_keys_returns_empty() -> None:
    res = get_option_greeks(instrument_keys=[], access_token="t")
    assert res["success"] is True and res["data"] == {}


def test_historical_rejects_bad_unit() -> None:
    res = get_historical_candles(
        instrument_key="NSE_INDEX|Nifty 50",
        unit="centuries",
        interval=1,
        to_date="2026-04-29",
        access_token="t",
    )
    assert res["success"] is False and "invalid_unit" in res["error"]
    res2 = get_intraday_candles(
        instrument_key="NSE_INDEX|Nifty 50", unit="frames", interval=1, access_token="t"
    )
    assert res2["success"] is False and "invalid_unit" in res2["error"]
