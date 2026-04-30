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
        "diff_at_signal": "10.5",
        "sum_ce_at_signal": "20",
        "sum_pe_at_signal": "30",
        "delta_at_signal": "10",
        "delta_pcr_at_signal": "None",
        "strategy_version": "abcd1234",
        "ts": "2026-04-30T01:23:45+00:00",
    }
    sig = asyncio.run(dispatcher._signal_from_payload(payload))
    assert sig is not None
    assert sig.sig_id == "abc"
    assert sig.intent == SignalIntent.FRESH_ENTRY
    assert sig.delta_pcr_at_signal is None
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
        "diff_at_signal": "5",
        "sum_ce_at_signal": "10",
        "sum_pe_at_signal": "0",
        "delta_at_signal": "-10",
        "delta_pcr_at_signal": "1.25",
        "strategy_version": "abcd1234",
        "ts": "2026-04-30T01:23:45+00:00",
    }
    sig = asyncio.run(dispatcher._signal_from_payload(payload))
    assert sig is not None
    assert sig.delta_pcr_at_signal == 1.25
    assert sig.intent == SignalIntent.REVERSAL_FLIP


def test_signal_from_payload_invalid_returns_none() -> None:
    sig = asyncio.run(dispatcher._signal_from_payload({"sig_id": "x"}))
    assert sig is None
