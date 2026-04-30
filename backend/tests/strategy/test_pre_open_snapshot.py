"""Pre-open snapshot capture and fail-closed gate."""

from __future__ import annotations

from typing import Any

import orjson

from engines.strategy import pre_open_snapshot
from state import keys as K


def _seed_chain(
    redis: Any, index: str, leaves: dict[int, dict[str, dict[str, Any] | None]]
) -> None:
    chain = {str(strike): sides for strike, sides in leaves.items()}
    redis.set(K.market_data_index_option_chain(index), orjson.dumps(chain))


def _seed_basket(redis: Any, index: str, ce: list[str], pe: list[str]) -> None:
    redis.set(K.strategy_basket(index), orjson.dumps({"ce": ce, "pe": pe}))


def test_capture_happy_path(fake_redis_sync: Any) -> None:
    leaves = {
        22950: {
            "ce": {
                "token": "CE_22950",
                "ltp": 110.5,
                "bid": 110.0,
                "ask": 111.0,
                "oi": 1000,
                "ts": 1000,
            },
            "pe": {
                "token": "PE_22950",
                "ltp": 30.0,
                "bid": 29.5,
                "ask": 30.5,
                "oi": 800,
                "ts": 1001,
            },
        },
        23000: {
            "ce": {
                "token": "CE_23000",
                "ltp": 80.0,
                "bid": 79.5,
                "ask": 80.5,
                "oi": 2000,
                "ts": 1002,
            },
            "pe": {
                "token": "PE_23000",
                "ltp": 50.0,
                "bid": 49.5,
                "ask": 50.5,
                "oi": 1500,
                "ts": 1003,
            },
        },
    }
    _seed_chain(fake_redis_sync, "nifty50", leaves)
    _seed_basket(fake_redis_sync, "nifty50", ["CE_22950", "CE_23000"], ["PE_22950", "PE_23000"])
    fake_redis_sync.set(K.strategy_enabled("nifty50"), "true")

    out = pre_open_snapshot.capture(fake_redis_sync, "nifty50")
    assert out["valid"] is True
    snap = out["snapshot"]
    assert set(snap.keys()) == {"CE_22950", "CE_23000", "PE_22950", "PE_23000"}
    assert snap["CE_23000"]["ltp"] == 80.0
    assert snap["CE_23000"]["ts"] == 1002

    persisted = orjson.loads(fake_redis_sync.get(K.strategy_pre_open("nifty50")))
    assert persisted == snap
    assert fake_redis_sync.get(K.strategy_enabled("nifty50")) == "true"


def test_capture_fail_closed_on_zero_ts(fake_redis_sync: Any) -> None:
    leaves = {
        22950: {
            "ce": {
                "token": "CE_22950",
                "ltp": 110.0,
                "bid": 109,
                "ask": 111,
                "oi": 100,
                "ts": 0,
            },
            "pe": {
                "token": "PE_22950",
                "ltp": 30.0,
                "bid": 29.5,
                "ask": 30.5,
                "oi": 800,
                "ts": 1001,
            },
        },
    }
    _seed_chain(fake_redis_sync, "nifty50", leaves)
    _seed_basket(fake_redis_sync, "nifty50", ["CE_22950"], ["PE_22950"])
    fake_redis_sync.set(K.strategy_enabled("nifty50"), "true")

    out = pre_open_snapshot.capture(fake_redis_sync, "nifty50")
    assert out["valid"] is False
    assert "CE_22950" in out["missing"]
    assert fake_redis_sync.get(K.strategy_enabled("nifty50")) == "false"


def test_capture_idempotent_reuses_existing(fake_redis_sync: Any) -> None:
    seed = {"CE_X": {"token": "CE_X", "ltp": 1.0, "ts": 5}}
    fake_redis_sync.set(K.strategy_pre_open("nifty50"), orjson.dumps(seed))
    _seed_basket(fake_redis_sync, "nifty50", ["CE_X"], [])

    out = pre_open_snapshot.capture(fake_redis_sync, "nifty50")
    assert out["valid"] is True
    assert out.get("reused") is True
    assert out["snapshot"] == seed


def test_capture_empty_basket_disables(fake_redis_sync: Any) -> None:
    _seed_basket(fake_redis_sync, "nifty50", [], [])
    fake_redis_sync.set(K.strategy_enabled("nifty50"), "true")
    out = pre_open_snapshot.capture(fake_redis_sync, "nifty50")
    assert out["valid"] is False
    assert fake_redis_sync.get(K.strategy_enabled("nifty50")) == "false"


def test_capture_token_missing_from_chain(fake_redis_sync: Any) -> None:
    leaves = {
        22950: {
            "ce": {
                "token": "CE_Y",
                "ltp": 100.0,
                "bid": 99,
                "ask": 101,
                "oi": 500,
                "ts": 1000,
            },
            "pe": None,
        }
    }
    _seed_chain(fake_redis_sync, "nifty50", leaves)
    _seed_basket(fake_redis_sync, "nifty50", ["CE_X"], [])
    fake_redis_sync.set(K.strategy_enabled("nifty50"), "true")

    out = pre_open_snapshot.capture(fake_redis_sync, "nifty50")
    assert out["valid"] is False
    assert "CE_X" in out["missing"]
    assert fake_redis_sync.get(K.strategy_enabled("nifty50")) == "false"
