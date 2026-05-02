"""Tests for the new `pre_entry_gate.check_and_reserve` behavior.

Verifies:
  * happy path passes both read-only gates and allocator gate.
  * each allocator failure mode bubbles up with `allocator_<reason>`.
  * `check()` (read-only) still passes when the allocator slot is full
    — the read-only gates are unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import orjson

from engines.order_exec import allocator, pre_entry_gate
from state import keys as K
from state.schemas.signal import Signal


def _signal(token: str = "NSE_FO|49520", index: str = "nifty50") -> Signal:
    return Signal(
        sig_id="abc123",
        index=index,
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


def _seed_chain(
    redis: Any,
    index: str,
    token: str,
    *,
    ltp: float,
    bid: float,
    ask: float,
    ask_qty: int = 1500,
) -> None:
    chain = {
        "23000": {
            "ce": {
                "token": token, "ltp": ltp, "bid": bid, "ask": ask,
                "bid_qty": 1500, "ask_qty": ask_qty, "vol": 0, "oi": 0, "ts": 1,
            },
            "pe": None,
        }
    }
    redis.set(K.market_data_index_option_chain(index), orjson.dumps(chain))


def _seed_full_world(
    redis: Any,
    *,
    trading_capital_inr: float = 200_000.0,
    max_concurrent_positions: int = 2,
    lot_size: int = 75,
) -> None:
    redis.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    redis.set(K.system_flag_engine_up("order_exec"), "true")
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({"spread_skip_pct": 0.05}))
    redis.set(
        K.STRATEGY_CONFIGS_RISK,
        orjson.dumps({
            "trading_capital_inr": trading_capital_inr,
            "max_concurrent_positions": max_concurrent_positions,
            "daily_loss_circuit_pct": 0.08,
        }),
    )
    redis.set(
        K.strategy_config_index("nifty50"),
        orjson.dumps({"index": "nifty50", "lot_size": lot_size}),
    )


def test_check_and_reserve_happy(fake_redis_sync: Any) -> None:
    _seed_full_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99, ask=101)
    ok, reason, premium = pre_entry_gate.check_and_reserve(fake_redis_sync, _signal())
    assert ok is True
    assert reason == "ok"
    # premium = 1 lot * 75 * ask(101) = 7575
    assert premium == 1 * 75 * 101


def test_check_and_reserve_blocks_when_no_risk_config(fake_redis_sync: Any) -> None:
    fake_redis_sync.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    fake_redis_sync.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    fake_redis_sync.set(K.system_flag_engine_up("order_exec"), "true")
    fake_redis_sync.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({"spread_skip_pct": 0.05}))
    fake_redis_sync.set(
        K.strategy_config_index("nifty50"),
        orjson.dumps({"index": "nifty50", "lot_size": 75}),
    )
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99, ask=101)
    ok, reason, premium = pre_entry_gate.check_and_reserve(fake_redis_sync, _signal())
    assert ok is False
    assert reason == "allocator_no_capital_configured"
    assert premium == 0.0


def test_check_and_reserve_blocks_insufficient_capital(fake_redis_sync: Any) -> None:
    _seed_full_world(fake_redis_sync, trading_capital_inr=1_000.0)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99, ask=101)
    ok, reason, premium = pre_entry_gate.check_and_reserve(fake_redis_sync, _signal())
    assert ok is False
    assert reason == "allocator_insufficient_capital"
    assert premium == 0.0


def test_check_and_reserve_blocks_already_open(fake_redis_sync: Any) -> None:
    _seed_full_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99, ask=101)
    # Pre-reserve directly via allocator for the same index.
    allocator.check_and_reserve(
        fake_redis_sync,
        index="nifty50",
        premium_required_inr=5_000.0,
        trading_capital_inr=200_000.0,
        max_concurrent_positions=2,
    )
    ok, reason, _premium = pre_entry_gate.check_and_reserve(fake_redis_sync, _signal())
    assert ok is False
    assert reason == "allocator_already_open_on_index"


def test_check_and_reserve_passes_through_readonly_failure(fake_redis_sync: Any) -> None:
    """Read-only gate failure must short-circuit BEFORE the allocator runs."""
    _seed_full_world(fake_redis_sync)
    fake_redis_sync.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99, ask=101)
    ok, reason, premium = pre_entry_gate.check_and_reserve(fake_redis_sync, _signal())
    assert ok is False
    assert reason == "trading_inactive"
    assert premium == 0.0
    # Allocator must NOT have reserved anything.
    assert fake_redis_sync.sismember(K.ORDERS_ALLOCATOR_OPEN_SYMBOLS, "nifty50") in (0, False)
