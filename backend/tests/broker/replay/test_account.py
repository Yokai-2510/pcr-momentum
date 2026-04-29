"""Replay tests for profile / capital / kill_switch / static_ips."""

from __future__ import annotations

import httpx
import respx

from brokers.upstox.capital import get_capital
from brokers.upstox.kill_switch import get_kill_switch_status, set_kill_switch
from brokers.upstox.profile import get_profile
from brokers.upstox.static_ips import get_static_ips


@respx.mock
def test_get_profile_happy() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "email": "u@x.com",
                    "user_id": "U1",
                    "exchanges": ["NSE", "NFO"],
                    "products": ["I", "D"],
                    "is_active": True,
                },
            },
        )
    )
    res = get_profile(access_token="t")
    assert res["success"] is True
    assert res["data"]["user_id"] == "U1"


@respx.mock
def test_get_profile_401() -> None:
    respx.get("https://api.upstox.com/v2/user/profile").mock(
        return_value=httpx.Response(401, json={"status": "error"})
    )
    res = get_profile(access_token="bad")
    assert res["success"] is False and res["code"] == 401


@respx.mock
def test_get_capital_happy() -> None:
    respx.get("https://api.upstox.com/v3/user/get-funds-and-margin").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"available_to_trade": {"total": 12345.0}},
            },
        )
    )
    res = get_capital(access_token="t")
    assert res["success"] is True
    assert res["data"]["available_to_trade"]["total"] == 12345.0


@respx.mock
def test_get_capital_maintenance_window() -> None:
    respx.get("https://api.upstox.com/v3/user/get-funds-and-margin").mock(
        return_value=httpx.Response(423)
    )
    res = get_capital(access_token="t")
    assert res["success"] is False
    assert res["error"] == "MAINTENANCE_WINDOW"
    assert res["code"] == 423


@respx.mock
def test_kill_switch_status_happy() -> None:
    respx.get("https://api.upstox.com/v2/user/kill-switch").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "segment": "NSE_FO",
                        "segment_status": "ACTIVE",
                        "kill_switch_enabled": False,
                    }
                ],
            },
        )
    )
    res = get_kill_switch_status(access_token="t")
    assert res["success"] is True
    assert res["data"][0]["segment"] == "NSE_FO"


@respx.mock
def test_kill_switch_set_passthrough() -> None:
    respx.post("https://api.upstox.com/v2/user/kill-switch").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "segment": "NSE_FO",
                        "segment_status": "ACTIVE",
                        "kill_switch_enabled": True,
                    }
                ],
            },
        )
    )
    res = set_kill_switch([{"segment": "NSE_FO", "action": "ENABLE"}], access_token="t")
    assert res["success"] is True
    assert res["data"][0]["kill_switch_enabled"] is True


@respx.mock
def test_static_ips_get_happy() -> None:
    respx.get("https://api.upstox.com/v2/user/ip").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "primary_ip": "3.6.128.21",
                    "secondary_ip": "38.254.178.144",
                    "primary_ip_updated_at": "2026-04-28 11:36:53",
                    "secondary_ip_updated_at": "2026-04-28 11:36:53",
                },
            },
        )
    )
    res = get_static_ips(access_token="t")
    assert res["success"] is True
    assert res["data"]["primary_ip"] == "3.6.128.21"


@respx.mock
def test_static_ips_get_error() -> None:
    respx.get("https://api.upstox.com/v2/user/ip").mock(
        return_value=httpx.Response(403, json={"status": "error"})
    )
    res = get_static_ips(access_token="t")
    assert res["success"] is False and res["code"] == 403
