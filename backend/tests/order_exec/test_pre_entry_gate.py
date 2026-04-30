"""pre_entry_gate — gates only, no I/O beyond Redis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import orjson

from engines.order_exec import pre_entry_gate
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


def _seed_chain(redis: Any, index: str, token: str, *, ltp: float, bid: float, ask: float,
                ask_qty: int = 1500) -> None:
    chain = {
        "23000": {
            "ce": {
                "token": token,
                "ltp": ltp,
                "bid": bid,
                "ask": ask,
                "bid_qty": 1500,
                "ask_qty": ask_qty,
                "vol": 0,
                "oi": 0,
                "ts": 1,
            },
            "pe": None,
        }
    }
    redis.set(K.market_data_index_option_chain(index), orjson.dumps(chain))


def _seed_world(redis: Any, *, trading_active: bool = True, dlc: bool = False,
                kill_switch: bool = False, engine_up: bool = True) -> None:
    redis.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true" if trading_active else "false")
    redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "true" if dlc else "false")
    redis.set(K.system_flag_engine_up("order_exec"), "true" if engine_up else "false")
    if kill_switch:
        redis.set(
            K.USER_CAPITAL_KILL_SWITCH,
            orjson.dumps([
                {"segment": "NSE_FO", "segment_status": "ACTIVE", "kill_switch_enabled": True}
            ]),
        )
    else:
        redis.delete(K.USER_CAPITAL_KILL_SWITCH)
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({"spread_skip_pct": 0.05}))


def test_gate_pass_happy(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99.5, ask=100.5)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is True and reason == "ok"


def test_gate_blocks_trading_inactive(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync, trading_active=False)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99.5, ask=100.5)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "trading_inactive"


def test_gate_blocks_daily_loss_circuit(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync, dlc=True)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99.5, ask=100.5)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "daily_loss_circuit"


def test_gate_blocks_kill_switch(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync, kill_switch=True)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99.5, ask=100.5)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "kill_switch_engaged"


def test_gate_blocks_engine_not_up(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync, engine_up=False)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=99.5, ask=100.5)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "order_exec_not_up"


def test_gate_blocks_missing_leaf(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync)
    # No chain seeded
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "leaf_missing"


def test_gate_blocks_no_quotes(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=0, bid=0, ask=0)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "no_quotes"


def test_gate_blocks_crossed_book(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=101, ask=99)
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason == "crossed_book"


def test_gate_blocks_wide_spread(fake_redis_sync: Any) -> None:
    _seed_world(fake_redis_sync)
    _seed_chain(fake_redis_sync, "nifty50", "NSE_FO|49520", ltp=100, bid=90, ask=110)  # 20%
    ok, reason = pre_entry_gate.check(fake_redis_sync, _signal())
    assert ok is False and reason.startswith("spread_too_wide")
