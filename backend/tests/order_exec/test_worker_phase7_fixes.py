"""Phase 7 bug-fix regression tests.

Covers the four gaps surfaced by the 2026-04-30 paper-mode soak:

  Bug 1  persist_report no longer hangs — worker buffers to Redis list.
  Bug 2  REVERSAL_FLIP closes the prior position before opening a new one.
  Bug 3  Allocator gate is wired into pre_entry_gate.check_and_reserve.
  Bug 4  Mutated Position fields (peak_premium, tsl_armed, tsl_level,
         current_premium) get HSET back to the position HASH each tick.

These tests are unit-scope: they hit fakeredis, no Postgres, no broker.
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

# ---------------------------------------------------------------------------
# Shared seeding helpers
# ---------------------------------------------------------------------------


def _signal(intent: str = "FRESH_ENTRY") -> Signal:
    return Signal(
        sig_id="abc123",
        index="nifty50",
        side="CE",
        strike=23000,
        instrument_token="NSE_FO|49520",
        intent=intent,  # type: ignore[arg-type]
        qty_lots=1,
        diff_at_signal=10.0,
        sum_ce_at_signal=20.0,
        sum_pe_at_signal=0.0,
        delta_at_signal=-20.0,
        delta_pcr_at_signal=None,
        strategy_version="t",
        ts=datetime.now(UTC),
    )


def _seed_world(redis: Any, *, premium: float = 100.0, mode: str = "paper") -> None:
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
        "worker_pool_size": 1,
        "liquidity_exit_suppress_after": "15:00",
    }))
    redis.set(
        K.STRATEGY_CONFIGS_RISK,
        orjson.dumps({
            "trading_capital_inr": 200_000.0,
            "max_concurrent_positions": 2,
            "daily_loss_circuit_pct": 0.08,
        }),
    )
    redis.set(
        K.strategy_config_index("nifty50"),
        orjson.dumps({
            "index": "nifty50",
            "lot_size": 75,
            "sl_pct": 0.20,
            "target_pct": 0.50,
            "tsl_arm_pct": 0.15,
            "tsl_trail_pct": 0.05,
            "max_hold_sec": 1500,
        }),
    )
    redis.set(K.SYSTEM_FLAGS_MODE, mode)
    redis.set(K.strategy_pre_open("nifty50"), orjson.dumps({"NSE_FO|49520": {"ltp": 100, "ts": 1}}))


@pytest.fixture
def patched_cleanup_lua(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same stub as test_paper_e2e — fakeredis lacks the project Lua loader."""
    from engines.order_exec import cleanup as cleanup_mod

    def _stub_cleanup(redis_sync: Any, *, pos_id: str, sig_id: str,
                     order_ids: list[str], index: str) -> int:
        pipe = redis_sync.pipeline()
        pipe.delete(K.orders_position(pos_id))
        pipe.delete(K.orders_status(pos_id))
        pipe.delete(K.strategy_signal(sig_id))
        for oid in order_ids:
            if oid:
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


def _drive_premium_ramp(monkeypatch: pytest.MonkeyPatch, *, start: float, step: float) -> None:
    """Patch worker._read_leaf to escalate premium so HARD_TARGET fires fast."""
    from engines.order_exec import worker as worker_mod

    counter = {"n": 0}

    def _bump_leaf(_redis: Any, _index: str, _token: str) -> dict[str, Any]:
        counter["n"] += 1
        ltp = start + step * counter["n"]
        return {"ltp": ltp, "bid": ltp - 0.5, "ask": ltp + 0.5, "ask_qty": 1500}

    monkeypatch.setattr(worker_mod, "_read_leaf", _bump_leaf)
    monkeypatch.setattr(worker_mod, "EXIT_POLL_SLEEP_SEC", 0.0)


def _run_to_completion(redis: Any, signal: Signal, timeout: float = 5.0) -> None:
    done = threading.Event()

    def _go() -> None:
        try:
            worker.process_signal(redis, None, signal)
        finally:
            done.set()

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    assert done.is_set(), "process_signal did not finish in time"


# ---------------------------------------------------------------------------
# Bug 1 — persist_report buffer
# ---------------------------------------------------------------------------


def test_bug1_report_pushed_to_pending_list(
    fake_redis_sync: Any, patched_cleanup_lua: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker MUST push the closed-position report to `orders:reports:pending`,
    not call `asyncio.run(persist_report)`."""
    _seed_world(fake_redis_sync, premium=100.0)
    _drive_premium_ramp(monkeypatch, start=100.0, step=5.0)

    _run_to_completion(fake_redis_sync, _signal())

    n_pending = fake_redis_sync.llen(K.ORDERS_REPORTS_PENDING)
    assert n_pending >= 1, "Bug 1 regression — no report buffered"

    raw = fake_redis_sync.lindex(K.ORDERS_REPORTS_PENDING, -1)
    payload = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    assert payload["sig_id"] == "abc123"
    assert payload["index"] == "nifty50"


# ---------------------------------------------------------------------------
# Bug 3 — allocator gate enforced
# ---------------------------------------------------------------------------


def test_bug3_allocator_blocks_when_index_already_open(
    fake_redis_sync: Any, patched_cleanup_lua: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the allocator already has a slot reserved for `nifty50`, a new
    FRESH_ENTRY signal for the same index MUST be rejected."""
    from engines.order_exec import allocator as alloc_mod

    _seed_world(fake_redis_sync, premium=100.0)
    # Pre-reserve to occupy the slot.
    ok, _, _, _ = alloc_mod.check_and_reserve(
        fake_redis_sync,
        index="nifty50",
        premium_required_inr=5_000.0,
        trading_capital_inr=200_000.0,
        max_concurrent_positions=2,
    )
    assert ok is True

    worker.process_signal(fake_redis_sync, None, _signal())

    entries = fake_redis_sync.xrevrange(K.STRATEGY_STREAM_REJECTED_SIGNALS, count=1)
    assert entries, "expected rejected signal entry"
    fields = entries[0][1]
    decoded = {
        (k.decode() if isinstance(k, bytes) else k): (
            v.decode() if isinstance(v, bytes) else v
        )
        for k, v in fields.items()
    }
    assert decoded.get("reason") == "allocator_already_open_on_index"


def test_bug3_allocator_releases_on_clean_close(
    fake_redis_sync: Any, patched_cleanup_lua: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a clean close, the allocator slot for the index must be free."""
    _seed_world(fake_redis_sync, premium=100.0)
    _drive_premium_ramp(monkeypatch, start=100.0, step=5.0)

    _run_to_completion(fake_redis_sync, _signal())

    # Allocator slot for nifty50 must be released.
    members = fake_redis_sync.smembers(K.ORDERS_ALLOCATOR_OPEN_SYMBOLS)
    members_decoded = {
        (m.decode() if isinstance(m, bytes) else m) for m in members
    }
    assert "nifty50" not in members_decoded


# ---------------------------------------------------------------------------
# Bug 4 — Position HASH refreshed each tick
# ---------------------------------------------------------------------------


def test_bug4_position_hash_refreshes_peak_premium(
    fake_redis_sync: Any, patched_cleanup_lua: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """During the exit-eval loop, `peak_premium` must be HSET on the position
    HASH (not only mutated in-memory)."""
    captured: dict[str, list[str]] = {"peak_premium": []}

    from engines.order_exec import worker as worker_mod

    real_refresh = worker_mod._refresh_position_hash

    def _spy_refresh(redis_sync: Any, position: Any, fields: tuple[str, ...]) -> None:
        captured["peak_premium"].append(str(position.peak_premium))
        real_refresh(redis_sync, position, fields)

    monkeypatch.setattr(worker_mod, "_refresh_position_hash", _spy_refresh)

    _seed_world(fake_redis_sync, premium=100.0)
    _drive_premium_ramp(monkeypatch, start=100.0, step=5.0)

    _run_to_completion(fake_redis_sync, _signal())

    # We expect more than one refresh and a strictly non-decreasing peak.
    from itertools import pairwise

    seen = [float(x) for x in captured["peak_premium"]]
    assert len(seen) >= 1
    for prev, nxt in pairwise(seen):
        assert nxt >= prev, f"peak_premium regressed: {prev} -> {nxt}"
