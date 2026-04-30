"""End-to-end paper-mode lifecycle: signal → close → cleanup against fakeredis.

We don't go through the dispatcher (no real Redis stream consumer-group
needed) — we directly invoke `worker.process_signal` after seeding the world.
We do NOT pass a Postgres pool, so persist_report is skipped and we can
verify the rest of the pipeline without DB.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import orjson
import pytest

from engines.order_exec import worker
from state import keys as K
from state.schemas.signal import Signal


def _signal(token: str = "NSE_FO|49520") -> Signal:
    return Signal(
        sig_id="abc123",
        index="nifty50",
        side="CE",
        strike=23000,
        instrument_token=token,
        intent="FRESH_ENTRY",
        qty_lots=1,
        diff_at_signal=10.0,
        sum_ce_at_signal=20.0,
        sum_pe_at_signal=0.0,
        delta_at_signal=-20.0,
        delta_pcr_at_signal=None,
        strategy_version="t",
        ts=datetime.now(UTC),
    )


def _seed_world(redis: Any, *, premium: float, mode: str = "paper") -> None:
    chain = {
        "23000": {
            "ce": {
                "token": "NSE_FO|49520",
                "ltp": premium,
                "bid": premium - 0.5,
                "ask": premium + 0.5,
                "bid_qty": 1500,
                "ask_qty": 1500,
                "vol": 0,
                "oi": 0,
                "ts": 1,
            },
            "pe": None,
        }
    }
    redis.set(K.market_data_index_option_chain("nifty50"), orjson.dumps(chain))
    redis.hset(K.market_data_index_spot("nifty50"), "ltp", str(23000))
    redis.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    redis.set(K.system_flag_engine_up("order_exec"), "true")
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({
        "spread_skip_pct": 0.05,
        "buffer_inr": 2.0,
        "eod_buffer_inr": 5.0,
        "drift_threshold_inr": 3.0,
        "chase_ceiling_inr": 15.0,
        "open_timeout_sec": 8,
        "partial_grace_sec": 3,
        "max_retries": 2,
        "worker_pool_size": 1,
        "liquidity_exit_suppress_after": "15:00",
    }))
    redis.set(K.strategy_config_index("nifty50"), orjson.dumps({
        "index": "nifty50",
        "strike_step": 50,
        "lot_size": 75,
        "exchange": "NFO",
        "pre_open_subscribe_window": 6,
        "trading_basket_range": 2,
        "reversal_threshold_inr": 20.0,
        "entry_dominance_threshold_inr": 20.0,
        "post_sl_cooldown_sec": 60,
        "post_reversal_cooldown_sec": 90,
        "max_entries_per_day": 8,
        "max_reversals_per_day": 4,
        "qty_lots": 1,
        "sl_pct": 0.20,
        "target_pct": 0.50,
        "tsl_arm_pct": 0.15,
        "tsl_trail_pct": 0.05,
        "max_hold_sec": 1500,
        "delta_pcr_required_for_entry": False,
    }))
    redis.set(K.SYSTEM_FLAGS_MODE, mode)
    redis.set(K.strategy_pre_open("nifty50"), orjson.dumps({
        "NSE_FO|49520": {"ltp": 100, "ts": 1},
    }))


@pytest.fixture
def patched_cleanup_lua(monkeypatch: pytest.MonkeyPatch) -> None:
    """fakeredis lacks the Lua loader plumbing; stub cleanup() to a plain
    Python equivalent that mutates the same keys deterministically."""
    from engines.order_exec import cleanup as cleanup_mod

    def _stub_cleanup(redis_sync: Any, *, pos_id: str, sig_id: str,
                     order_ids: list[str], index: str) -> int:
        pipe = redis_sync.pipeline()
        pipe.delete(K.orders_position(pos_id))
        pipe.delete(K.orders_status(pos_id))
        pipe.delete(K.strategy_signal(sig_id))
        for oid in order_ids:
            if not oid:
                continue
            pipe.delete(K.orders_order(oid))
            pipe.delete(K.orders_broker_pos(oid))
            pipe.srem(K.ORDERS_BROKER_OPEN_ORDERS, oid)
        pipe.srem(K.ORDERS_POSITIONS_OPEN, pos_id)
        pipe.srem(K.orders_positions_open_by_index(index), pos_id)
        pipe.sadd(K.ORDERS_POSITIONS_CLOSED_TODAY, pos_id)
        pipe.delete(K.strategy_current_position_id(index))
        pipe.srem(K.STRATEGY_SIGNALS_ACTIVE, sig_id)
        pipe.execute()
        return 1

    monkeypatch.setattr(cleanup_mod, "cleanup", _stub_cleanup)


def test_paper_e2e_target_hit_closes_cleanly(
    fake_redis_sync: Any, patched_cleanup_lua: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_world(fake_redis_sync, premium=100.0)
    redis = fake_redis_sync

    # Drive the in-memory chain LTP up so the exit-eval cascade hits HARD_TARGET
    # quickly. We patch _read_leaf to escalate the premium each call.
    from engines.order_exec import worker as worker_mod
    counter = {"n": 0}

    def _bump_leaf(_redis, _index, _token):
        counter["n"] += 1
        # 1st call (entry): ask=100.5; subsequent calls (exit_eval): ramp LTP.
        ltp = 100.0 + 5 * counter["n"]
        return {"ltp": ltp, "bid": ltp - 0.5, "ask": ltp + 0.5, "ask_qty": 1500}

    monkeypatch.setattr(worker_mod, "_read_leaf", _bump_leaf)
    monkeypatch.setattr(worker_mod, "EXIT_POLL_SLEEP_SEC", 0.0)

    # Run process_signal — should complete in well under a second.
    done = threading.Event()

    def _run() -> None:
        try:
            worker.process_signal(redis, None, _signal())
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    done.wait(timeout=5.0)
    assert done.is_set(), "process_signal did not finish"

    # Position should be cleaned up: no membership, but pos_id in closed_today.
    open_set = redis.smembers(K.ORDERS_POSITIONS_OPEN)
    closed_today = redis.smembers(K.ORDERS_POSITIONS_CLOSED_TODAY)
    assert len(open_set) == 0
    assert len(closed_today) == 1


def test_paper_e2e_pre_entry_gate_blocks(
    fake_redis_sync: Any, patched_cleanup_lua: None
) -> None:
    _seed_world(fake_redis_sync, premium=100.0)
    fake_redis_sync.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")  # block

    worker.process_signal(fake_redis_sync, None, _signal())

    # Should have written a rejected signal entry on the audit stream.
    entries = fake_redis_sync.xrevrange(K.STRATEGY_STREAM_REJECTED_SIGNALS, count=1)
    assert entries
    fields = entries[0][1]
    decoded = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
               for k, v in fields.items()}
    assert decoded.get("reason") == "trading_inactive"
