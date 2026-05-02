"""Tests for engines.health.probes — pure classifier behavior.

Tests focus on the worst-of aggregation and the heartbeat-staleness
classifier; system_load / swap probes are smoke-checked only since they
just read psutil.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from engines.health import probes
from state import keys as K


def test_aggregate_status_picks_worst() -> None:
    assert probes.aggregate_status([("green", "ok"), ("green", "ok")]) == "green"
    assert probes.aggregate_status([("green", "ok"), ("yellow", "x")]) == "yellow"
    assert probes.aggregate_status([("yellow", "x"), ("red", "boom")]) == "red"
    assert probes.aggregate_status([]) == "red"


@pytest.mark.asyncio
async def test_probe_redis_green(fake_redis_async: Any) -> None:
    status, _detail = await probes.probe_redis(fake_redis_async)
    assert status == "green"


@pytest.mark.asyncio
async def test_probe_engines_classifies_freshness(
    fake_redis_async: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_ms = int(time.time() * 1000)
    # init: fresh (1s old)
    await fake_redis_async.hset(K.SYSTEM_HEALTH_HEARTBEATS, "init", str(now_ms - 1_000))
    # data_pipeline: yellow zone (20s old)
    await fake_redis_async.hset(
        K.SYSTEM_HEALTH_HEARTBEATS, "data_pipeline", str(now_ms - 20_000)
    )
    # strategy: red zone (60s old)
    await fake_redis_async.hset(
        K.SYSTEM_HEALTH_HEARTBEATS, "strategy", str(now_ms - 60_000)
    )

    res = await probes.probe_engines(fake_redis_async)
    assert res["init"][0] == "green"
    assert res["data_pipeline"][0] == "yellow"
    assert res["strategy"][0] == "red"


@pytest.mark.asyncio
async def test_probe_broker_ws_red_when_missing(fake_redis_async: Any) -> None:
    status, _detail = await probes.probe_broker_ws(fake_redis_async)
    assert status == "red"


@pytest.mark.asyncio
async def test_probe_broker_ws_green_when_fresh(fake_redis_async: Any) -> None:
    now_ms = int(time.time() * 1000)
    await fake_redis_async.hset(
        K.MARKET_DATA_WS_STATUS_MARKET,
        mapping={"state": "connected", "ts_ms": str(now_ms)},
    )
    status, detail = await probes.probe_broker_ws(fake_redis_async)
    assert status == "green"
    assert "connected" in detail


def test_probe_system_load_returns_known_status() -> None:
    status, _detail = probes.probe_system_load()
    assert status in ("green", "yellow", "red")


def test_probe_swap_returns_known_status() -> None:
    status, _detail = probes.probe_swap()
    assert status in ("green", "yellow", "red")
