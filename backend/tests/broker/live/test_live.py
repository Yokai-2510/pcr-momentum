"""
Live broker SDK smoke tests.

GATED: only run when UPSTOX_ACCESS_TOKEN is set (and pytest -m live, or
pytest backend/tests/broker/live).

Read-only paths only:
  - get_profile
  - get_capital   (returns MAINTENANCE_WINDOW between 00:00 and 05:30 IST)
  - get_option_contracts + nearest_expiry → get_option_chain
  - market_streamer (skipped here; live WS test runs separately)

Never calls place_order / modify_order / cancel_order / set_kill_switch /
update_static_ips / exit_all_positions.
"""

from __future__ import annotations

import os

import pytest

from brokers.upstox import UpstoxAPI

pytestmark = pytest.mark.live

TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")

skip_no_token = pytest.mark.skipif(not TOKEN, reason="UPSTOX_ACCESS_TOKEN not set")


@skip_no_token
def test_validate_token_remote() -> None:
    assert UpstoxAPI.validate_token({"access_token": TOKEN}) is True


@skip_no_token
def test_get_profile_returns_user() -> None:
    res = UpstoxAPI.get_profile({"access_token": TOKEN})
    if not res["success"]:
        pytest.skip(f"profile not available: {res['error']}")
    assert "user_id" in (res["data"] or {})


@skip_no_token
def test_get_capital_handles_maintenance_window() -> None:
    res = UpstoxAPI.get_capital({"access_token": TOKEN})
    # Either success (data dict) or graceful MAINTENANCE_WINDOW error
    assert res["success"] is True or res["error"] == "MAINTENANCE_WINDOW"


@skip_no_token
def test_get_option_chain_nifty_nearest_expiry_non_empty() -> None:
    cres = UpstoxAPI.get_option_contracts(
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "access_token": TOKEN,
        }
    )
    if not cres["success"]:
        pytest.skip(f"option_contracts unavailable: {cres['error']}")
    expiry = UpstoxAPI.nearest_expiry(cres["data"])
    if not expiry:
        pytest.skip("no future expiry returned")
    chain = UpstoxAPI.get_option_chain(
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "expiry_date": expiry,
            "access_token": TOKEN,
        }
    )
    assert chain["success"] is True, chain["error"]
    assert isinstance(chain["data"], list) and len(chain["data"]) > 0


@skip_no_token
def test_total_pcr_is_finite_or_none() -> None:
    cres = UpstoxAPI.get_option_contracts(
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "access_token": TOKEN,
        }
    )
    if not cres["success"]:
        pytest.skip("contracts call failed")
    expiry = UpstoxAPI.nearest_expiry(cres["data"])
    if not expiry:
        pytest.skip("no expiry")
    chain = UpstoxAPI.get_option_chain(
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "expiry_date": expiry,
            "access_token": TOKEN,
        }
    )
    pcr = UpstoxAPI.total_pcr(chain["data"]) if chain["success"] else None
    # Off-market hours return zero call OI ⇒ None is acceptable; otherwise
    # should be a positive finite number.
    assert pcr is None or pcr > 0
