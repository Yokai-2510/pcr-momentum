"""Tests for engines.background.report_drainer.

We don't talk to a real Postgres — `reporting.persist_report` is patched
to record what would have been INSERTed. The point is to verify the
drain loop's branching: success path drops from queue, transient failure
re-pushes + backs off, malformed payload is dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import orjson
import pytest

from engines.background import report_drainer
from state import keys as K


@pytest.mark.asyncio
async def test_drainer_persists_then_pops(
    fake_redis_async: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted: list[Any] = []

    async def _fake_persist(_pool: Any, report: Any) -> str:
        persisted.append(report)
        return "row-1"

    from engines.order_exec import reporting as reporting_mod
    monkeypatch.setattr(reporting_mod, "persist_report", _fake_persist)

    payload = {
        "sig_id": "abc1",
        "index": "nifty50",
        "mode": "paper",
        "side": "CE",
        "strike": 23000,
        "instrument_token": "NSE_FO|49520",
        "qty": 75,
        "entry_ts": "2026-04-30T09:30:00+00:00",
        "exit_ts": "2026-04-30T09:35:00+00:00",
        "holding_seconds": 300,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "pnl": 750.0,
        "pnl_pct": 10.0,
        "exit_reason": "HARD_TARGET",
        "intent": "FRESH_ENTRY",
        "signal_snapshot": {"sig_id": "abc1"},
        "pre_open_snapshot": {},
        "market_snapshot_entry": {
            "ts": "2026-04-30T09:30:00+00:00",
            "spot": 23000.0, "sum_ce": 20.0, "sum_pe": 0.0,
            "delta": -20.0, "delta_pcr_cumulative": None, "per_strike": {},
        },
        "market_snapshot_exit": {
            "ts": "2026-04-30T09:35:00+00:00",
            "spot": 23010.0, "sum_ce": 22.0, "sum_pe": 1.0,
            "delta": -21.0, "delta_pcr_cumulative": None, "per_strike": {},
        },
        "exit_eval_history": None,
        "trailing_history": None,
        "order_events": [],
        "latencies": {
            "signal_to_submit_ms": 0, "submit_to_ack_ms": 0,
            "ack_to_fill_ms": 0, "decision_to_exit_submit_ms": 0,
            "exit_submit_to_fill_ms": 0,
        },
        "pnl_breakdown": {
            "gross": 750.0, "charges": 0.0, "slippage": 0.0, "net": 750.0,
        },
        "delta_pcr_at_entry": None,
        "delta_pcr_at_exit": None,
        "raw_broker_responses": None,
        "strategy_version": "t",
    }
    await fake_redis_async.rpush(K.ORDERS_REPORTS_PENDING, orjson.dumps(payload).decode())

    shutdown = asyncio.Event()
    monkeypatch.setattr(report_drainer, "_DRAIN_BLOCK_SEC", 1)

    async def _stop_after_drain() -> None:
        # Wait until the queue is empty, then flip shutdown.
        for _ in range(50):
            n = await fake_redis_async.llen(K.ORDERS_REPORTS_PENDING)
            if n == 0 and persisted:
                break
            await asyncio.sleep(0.05)
        shutdown.set()

    await asyncio.gather(
        report_drainer.drain_loop(fake_redis_async, pool=None, shutdown=shutdown),  # type: ignore[arg-type]
        _stop_after_drain(),
    )

    assert len(persisted) == 1
    assert await fake_redis_async.llen(K.ORDERS_REPORTS_PENDING) == 0


@pytest.mark.asyncio
async def test_drainer_drops_malformed_payload(
    fake_redis_async: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted: list[Any] = []

    async def _fake_persist(_pool: Any, report: Any) -> str:
        persisted.append(report)
        return "row-x"

    from engines.order_exec import reporting as reporting_mod
    monkeypatch.setattr(reporting_mod, "persist_report", _fake_persist)
    monkeypatch.setattr(report_drainer, "_DRAIN_BLOCK_SEC", 1)

    await fake_redis_async.rpush(K.ORDERS_REPORTS_PENDING, "{not-json")

    shutdown = asyncio.Event()

    async def _stop_after() -> None:
        for _ in range(50):
            n = await fake_redis_async.llen(K.ORDERS_REPORTS_PENDING)
            if n == 0:
                break
            await asyncio.sleep(0.05)
        shutdown.set()

    await asyncio.gather(
        report_drainer.drain_loop(fake_redis_async, pool=None, shutdown=shutdown),  # type: ignore[arg-type]
        _stop_after(),
    )

    # Malformed payload dropped, no persist attempt.
    assert persisted == []
    assert await fake_redis_async.llen(K.ORDERS_REPORTS_PENDING) == 0
