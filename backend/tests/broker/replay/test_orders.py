"""Replay tests for orders + positions."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.orders import (
    cancel_order,
    exit_all_positions,
    get_order_book,
    get_order_history,
    get_order_status,
    get_trades_by_order,
    get_trades_for_day,
    modify_order,
    place_order,
)
from brokers.upstox.positions import get_positions


@respx.mock
def test_place_order_happy() -> None:
    respx.post("https://api-hft.upstox.com/v3/order/place").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_ids": ["O1", "O1S2"]},
                "metadata": {"latency": 78},
            },
        )
    )
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=75,
        transaction_type="BUY",
        access_token="t",
        price=142.5,
    )
    assert res["success"] is True
    assert res["data"]["first_order_id"] == "O1"
    assert res["data"]["latency_ms"] == 78
    assert res["data"]["order_ids"] == ["O1", "O1S2"]


@respx.mock
def test_place_order_broker_reject() -> None:
    respx.post("https://api-hft.upstox.com/v3/order/place").mock(
        return_value=httpx.Response(400, json={"status": "error", "errors": [{"code": "X"}]})
    )
    res = place_order(
        instrument_token="NSE_FO|49520",
        quantity=75,
        transaction_type="BUY",
        access_token="t",
    )
    assert res["success"] is False
    assert res["code"] == 400


@respx.mock
def test_modify_order_happy_partial_fields() -> None:
    respx.put("https://api-hft.upstox.com/v3/order/modify").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_id": "O1"},
                "metadata": {"latency": 12},
            },
        )
    )
    res = modify_order(order_id="O1", access_token="t", price=145.0)
    assert res["success"] is True
    assert res["data"]["order_id"] == "O1"
    assert res["data"]["latency_ms"] == 12


@respx.mock
def test_cancel_order_happy() -> None:
    respx.delete("https://api-hft.upstox.com/v3/order/cancel").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"order_id": "O1"}, "metadata": {"latency": 4}}
        )
    )
    res = cancel_order(order_id="O1", access_token="t")
    assert res["success"] is True
    assert res["data"]["order_id"] == "O1"


@respx.mock
def test_get_order_status_happy() -> None:
    respx.get("https://api.upstox.com/v2/order/details").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_id": "O1", "status": "complete", "filled_quantity": 75},
            },
        )
    )
    res = get_order_status(order_id="O1", access_token="t")
    assert res["success"] is True
    assert res["data"]["status"] == "complete"


@respx.mock
def test_get_order_history_happy() -> None:
    respx.get("https://api.upstox.com/v2/order/history").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": [{"status": "open"}, {"status": "complete"}]}
        )
    )
    res = get_order_history(order_id="O1", access_token="t")
    assert res["success"] is True
    assert len(res["data"]) == 2


@respx.mock
def test_get_order_book_envelope_shape() -> None:
    respx.get("https://api.upstox.com/v2/order/retrieve-all").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": [{"order_id": "O1"}, {"order_id": "O2"}]}
        )
    )
    res = get_order_book(access_token="t")
    assert res["success"] is True
    assert len(res["data"]) == 2


@respx.mock
def test_get_order_book_bare_array_shape() -> None:
    respx.get("https://api.upstox.com/v2/order/retrieve-all").mock(
        return_value=httpx.Response(200, json=[{"order_id": "O3"}])
    )
    res = get_order_book(access_token="t")
    assert res["success"] is True
    assert len(res["data"]) == 1 and res["data"][0]["order_id"] == "O3"


@respx.mock
def test_get_order_book_5xx() -> None:
    respx.get("https://api.upstox.com/v2/order/retrieve-all").mock(
        return_value=httpx.Response(500, json={"status": "error"})
    )
    res = get_order_book(access_token="t")
    assert res["success"] is False


@respx.mock
def test_get_trades_for_day_happy() -> None:
    respx.get("https://api.upstox.com/v2/order/trades/get-trades-for-day").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [{"trade_id": "T1"}]})
    )
    res = get_trades_for_day(access_token="t")
    assert res["success"] is True
    assert res["data"][0]["trade_id"] == "T1"


@respx.mock
def test_get_trades_by_order_happy() -> None:
    respx.get("https://api.upstox.com/v2/order/trades").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [{"trade_id": "T2"}]})
    )
    res = get_trades_by_order(order_id="O1", access_token="t")
    assert res["success"] is True
    assert res["data"][0]["trade_id"] == "T2"


@respx.mock
def test_exit_all_positions_summarizes() -> None:
    respx.post("https://api.upstox.com/v2/order/positions/exit").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_ids": ["O1", "O2", "O3"]},
                "summary": {"total": 3, "success": 3, "error": 0},
            },
        )
    )
    res = exit_all_positions(access_token="t")
    assert res["success"] is True
    assert res["data"]["total"] == 3 and res["data"]["successful"] == 3


@respx.mock
def test_get_positions_happy() -> None:
    respx.get("https://api.upstox.com/v2/portfolio/short-term-positions").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": [{"trading_symbol": "INFY"}]}
        )
    )
    res = get_positions(access_token="t")
    assert res["success"] is True
    assert res["data"][0]["trading_symbol"] == "INFY"


@respx.mock
def test_get_positions_unauthorized() -> None:
    respx.get("https://api.upstox.com/v2/portfolio/short-term-positions").mock(
        return_value=httpx.Response(401, json={"status": "error"})
    )
    res = get_positions(access_token="bad")
    assert res["success"] is False and res["code"] == 401
