"""Replay tests for brokers.upstox.auth via respx."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.auth import is_token_valid_remote, request_access_token


@respx.mock
def test_validate_token_happy() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"user_id": "U"}})
    )
    assert is_token_valid_remote("good") is True


@respx.mock
def test_validate_token_401_returns_false() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        return_value=httpx.Response(401, json={"status": "error"})
    )
    assert is_token_valid_remote("bad") is False


@respx.mock
def test_validate_token_network_error_returns_false() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        side_effect=httpx.ConnectError("network down")
    )
    assert is_token_valid_remote("any") is False


def test_validate_token_empty_string_returns_false_without_request() -> None:
    assert is_token_valid_remote("") is False


@respx.mock
def test_request_access_token_happy() -> None:
    url = "https://api.upstox.com/v3/login/auth/token/request/CID"
    respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "authorization_expiry": "1734483600000",
                    "notifier_url": "https://hook.example/upstox",
                },
            },
        )
    )
    res = request_access_token({"api_key": "CID", "api_secret": "S"})
    assert res["success"] is True
    assert res["data"]["notifier_url"] == "https://hook.example/upstox"
    assert res["data"]["authorization_expiry"] == "1734483600000"


@respx.mock
def test_request_access_token_failure() -> None:
    url = "https://api.upstox.com/v3/login/auth/token/request/CID"
    respx.post(url).mock(
        return_value=httpx.Response(401, json={"status": "error", "errors": [{"errorCode": "X"}]})
    )
    res = request_access_token({"api_key": "CID", "api_secret": "S"})
    assert res["success"] is False
    assert res["code"] == 401


@respx.mock
def test_request_access_token_network_exception() -> None:
    url = "https://api.upstox.com/v3/login/auth/token/request/CID"
    respx.post(url).mock(side_effect=httpx.ConnectError("offline"))
    res = request_access_token({"api_key": "CID", "api_secret": "S"})
    assert res["success"] is False and "REQUEST_EXCEPTION" in res["error"]
