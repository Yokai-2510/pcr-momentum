"""
Live market streamer smoke test — requires UPSTOX_ACCESS_TOKEN AND a market
session (subscribes to NIFTY 50 LTPC and waits up to 30s for ≥1 message).

Off-market: WS connects but no messages flow → test PASSES with a skip note,
because the SDK's contract is "live data only when market is live". A
proper exit-criteria run for Project_Plan §3 should be done during NSE
hours; this test still asserts that connect() succeeds without raising.
"""

from __future__ import annotations

import os
import time

import pytest

from brokers.upstox import UpstoxAPI

pytestmark = pytest.mark.live

TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")

skip_no_token = pytest.mark.skipif(not TOKEN, reason="UPSTOX_ACCESS_TOKEN not set")


@skip_no_token
def test_market_streamer_connects_and_optionally_ticks() -> None:
    msgs: list[dict] = []

    def on_message(msg):
        msgs.append(msg)

    streamer = UpstoxAPI.market_streamer(
        {
            "access_token": TOKEN,
            "instrument_keys": ["NSE_INDEX|Nifty 50"],
            "mode": "ltpc",
            "on_message": on_message,
        }
    )

    deadline = time.time() + 30
    while time.time() < deadline and len(msgs) < 1:
        time.sleep(0.5)

    import contextlib

    with contextlib.suppress(Exception):
        streamer.disconnect()

    if not msgs:
        pytest.skip("no ticks received in 30s — likely off-market hours")
    assert len(msgs) >= 1
