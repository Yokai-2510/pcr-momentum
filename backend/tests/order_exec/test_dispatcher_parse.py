"""Dispatcher signal-parsing utility test (no async loop)."""

from __future__ import annotations

import asyncio

from engines.order_exec import dispatcher
from state.schemas.signal import SignalIntent


def test_signal_from_payload_round_trip() -> None:
    payload = {
        "sig_id": "abc",
        "index": "nifty50",
        "side": "PE",
        "strike": "23050",
        "instrument_token": "NSE_FO|49521",
        "intent": "FRESH_ENTRY",
        "qty_lots": "1",
        "metrics_at_signal": '{"sum_ce": 20, "sum_pe": 30, "delta": 10}',
        "ts": "2026-04-30T01:23:45+00:00",
    }
    sig = asyncio.run(dispatcher._signal_from_payload(payload))
    assert sig is not None
    assert sig.sig_id == "abc"
    assert sig.intent == SignalIntent.FRESH_ENTRY
    assert sig.qty_lots == 1


def test_signal_from_payload_with_delta_pcr() -> None:
    payload = {
        "sig_id": "abc",
        "index": "nifty50",
        "side": "CE",
        "strike": "23000",
        "instrument_token": "NSE_FO|49520",
        "intent": "REVERSAL_FLIP",
        "qty_lots": "2",
        "metrics_at_signal": '{"sum_ce": 10, "sum_pe": 0, "delta": -10, "delta_pcr": 1.25}',
        "ts": "2026-04-30T01:23:45+00:00",
    }
    sig = asyncio.run(dispatcher._signal_from_payload(payload))
    assert sig is not None
    assert sig.metrics_at_signal["delta_pcr"] == 1.25
    assert sig.intent == SignalIntent.REVERSAL_FLIP


def test_signal_from_payload_invalid_returns_none() -> None:
    sig = asyncio.run(dispatcher._signal_from_payload({"sig_id": "x"}))
    assert sig is None
