"""Strategy tick-to-signal tests against fakeredis."""

from __future__ import annotations

import time
from typing import Any

import orjson
import pytest

from engines.strategy.strategies.nifty50 import NIFTY50Strategy
from state import keys as K
from state.schemas.config import IndexConfig
from state.schemas.signal import SignalIntent


@pytest.fixture(autouse=True)
def _inside_entry_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """These unit tests exercise decisions, not the real wall-clock freeze."""
    monkeypatch.setattr("engines.strategy.strategies.base._hhmm", lambda *_args: "09:30")


def _config() -> IndexConfig:
    return IndexConfig.model_validate(
        {
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
        }
    )


def _strike(tok: str) -> str:
    return tok.split("_")[1]


def _seed_world(
    redis: Any,
    *,
    ce_ltps: dict[str, float],
    pe_ltps: dict[str, float],
    ce_baseline: dict[str, float],
    pe_baseline: dict[str, float],
) -> None:
    chain: dict[str, dict[str, dict[str, Any] | None]] = {}
    for tok, ltp in ce_ltps.items():
        chain.setdefault(_strike(tok), {"ce": None, "pe": None})
        chain[_strike(tok)]["ce"] = {
            "token": tok,
            "ltp": ltp,
            "bid": ltp - 0.5,
            "ask": ltp + 0.5,
            "bid_qty": 1500,
            "ask_qty": 1500,
            "vol": 0,
            "oi": 0,
            "ts": 1,
        }
    for tok, ltp in pe_ltps.items():
        chain.setdefault(_strike(tok), {"ce": None, "pe": None})
        chain[_strike(tok)]["pe"] = {
            "token": tok,
            "ltp": ltp,
            "bid": ltp - 0.5,
            "ask": ltp + 0.5,
            "bid_qty": 1500,
            "ask_qty": 1500,
            "vol": 0,
            "oi": 0,
            "ts": 1,
        }
    redis.set(K.market_data_index_option_chain("nifty50"), orjson.dumps(chain))
    redis.set(
        K.strategy_basket("nifty50"),
        orjson.dumps({"ce": list(ce_ltps.keys()), "pe": list(pe_ltps.keys())}),
    )

    pre_open: dict[str, dict[str, Any]] = {}
    for tok, ltp in ce_baseline.items():
        pre_open[tok] = {"token": tok, "ltp": ltp, "bid": 0, "ask": 0, "oi": 0, "ts": 1}
    for tok, ltp in pe_baseline.items():
        pre_open[tok] = {"token": tok, "ltp": ltp, "bid": 0, "ask": 0, "oi": 0, "ts": 1}
    redis.set(K.strategy_pre_open("nifty50"), orjson.dumps(pre_open))

    redis.set(K.SYSTEM_FLAGS_READY, "true")
    redis.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    redis.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    redis.set(K.strategy_enabled("nifty50"), "true")
    redis.set(K.strategy_state("nifty50"), "FLAT")
    redis.set(K.strategy_cooldown_until_ts("nifty50"), "0")
    redis.set(K.strategy_cooldown_reason("nifty50"), "")
    redis.set(K.strategy_counters_entries_today("nifty50"), "0")
    redis.set(K.strategy_counters_reversals_today("nifty50"), "0")
    redis.set(K.strategy_counters_wins_today("nifty50"), "0")
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({"spread_skip_pct": 0.05}))


def _new_instance(redis: Any) -> NIFTY50Strategy:
    return NIFTY50Strategy(redis, _config())


def _last_signal(redis: Any) -> dict[str, str] | None:
    entries = redis.xrevrange(K.STRATEGY_STREAM_SIGNALS, count=1)
    if not entries:
        return None
    _entry_id, fields = entries[0]
    out: dict[str, str] = {}
    for key, value in fields.items():
        k = key.decode() if isinstance(key, bytes) else str(key)
        v = value.decode() if isinstance(value, bytes) else str(value)
        out[k] = v
    return out


def test_entry_buy_pe_when_pe_dominates(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100, "CE_22950": 120, "CE_22900": 140},
        pe_ltps={"PE_23000": 80, "PE_23050": 70, "PE_23100": 65},
        ce_baseline={"CE_23000": 100, "CE_22950": 120, "CE_22900": 140},
        pe_baseline={"PE_23000": 50, "PE_23050": 40, "PE_23100": 30},
    )
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})

    sig = _last_signal(fake_redis_sync)
    assert sig is not None
    assert sig["index"] == "nifty50"
    assert sig["side"] == "PE"
    assert sig["intent"] == "FRESH_ENTRY"
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "IN_PE"
    assert fake_redis_sync.get(K.strategy_counters_entries_today("nifty50")) == "1"


def test_entry_buy_ce_when_ce_dominates(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 130, "CE_22950": 150, "CE_22900": 170},
        pe_ltps={"PE_23000": 50, "PE_23050": 40, "PE_23100": 30},
        ce_baseline={"CE_23000": 100, "CE_22950": 120, "CE_22900": 140},
        pe_baseline={"PE_23000": 50, "PE_23050": 40, "PE_23100": 30},
    )
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})

    sig = _last_signal(fake_redis_sync)
    assert sig is not None
    assert sig["side"] == "CE"
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "IN_CE"


def test_no_entry_when_both_negative(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 90},
        pe_ltps={"PE_23000": 40},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "FLAT"


def test_no_entry_when_ambiguous(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 110},
        pe_ltps={"PE_23000": 60},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None


def test_no_entry_when_system_gates_fail(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "true")
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "FLAT"


def test_entry_cap_blocks_after_max(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.strategy_counters_entries_today("nifty50"), "8")
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None


def test_liquidity_gate_blocks_thin_depth(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    chain = orjson.loads(fake_redis_sync.get(K.market_data_index_option_chain("nifty50")))
    chain["23000"]["pe"]["ask_qty"] = 10
    fake_redis_sync.set(K.market_data_index_option_chain("nifty50"), orjson.dumps(chain))

    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None


def test_flip_in_ce_when_pe_overtakes(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 110},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.strategy_state("nifty50"), "IN_CE")

    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})

    sig = _last_signal(fake_redis_sync)
    assert sig is not None
    assert sig["intent"] == "REVERSAL_FLIP"
    assert sig["side"] == "PE"
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "IN_PE"
    assert fake_redis_sync.get(K.strategy_counters_reversals_today("nifty50")) == "1"


def test_flip_held_below_threshold(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 60},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.strategy_state("nifty50"), "IN_CE")

    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None


def test_reversal_cap_blocks_5th_flip(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 110},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.strategy_state("nifty50"), "IN_CE")
    fake_redis_sync.set(K.strategy_counters_reversals_today("nifty50"), "4")

    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None


def test_cooldown_blocks_signal_emission(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    future_ms = int(time.time() * 1000) + 60_000
    fake_redis_sync.set(K.strategy_state("nifty50"), "COOLDOWN")
    fake_redis_sync.set(K.strategy_cooldown_until_ts("nifty50"), str(future_ms))

    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "COOLDOWN"


def test_maybe_exit_cooldown_transitions_to_flat(fake_redis_sync: Any) -> None:
    fake_redis_sync.set(K.strategy_state("nifty50"), "COOLDOWN")
    fake_redis_sync.set(K.strategy_cooldown_until_ts("nifty50"), "1")
    inst = _new_instance(fake_redis_sync)
    inst._maybe_exit_cooldown()
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "FLAT"


def test_halted_drops_all_ticks(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    fake_redis_sync.set(K.strategy_state("nifty50"), "HALTED")
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})
    assert _last_signal(fake_redis_sync) is None
    assert fake_redis_sync.get(K.strategy_state("nifty50")) == "HALTED"


def test_live_view_keys_updated_on_tick(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 130},
        pe_ltps={"PE_23000": 100},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    inst = _new_instance(fake_redis_sync)
    inst._on_tick("0-0", {})

    assert fake_redis_sync.get(K.strategy_live_sum_ce("nifty50")) == "30.0000"
    assert fake_redis_sync.get(K.strategy_live_sum_pe("nifty50")) == "50.0000"
    assert fake_redis_sync.get(K.strategy_live_delta("nifty50")) == "20.0000"
    view = orjson.loads(fake_redis_sync.get(K.ui_view_strategy("nifty50")))
    assert view["sum_ce"] == 30.0
    assert view["sum_pe"] == 50.0


def test_signal_idempotency_within_same_tick(fake_redis_sync: Any) -> None:
    _seed_world(
        fake_redis_sync,
        ce_ltps={"CE_23000": 100},
        pe_ltps={"PE_23000": 110},
        ce_baseline={"CE_23000": 100},
        pe_baseline={"PE_23000": 50},
    )
    inst = _new_instance(fake_redis_sync)

    sig_a = inst._emit_signal(
        intent=SignalIntent.REVERSAL_FLIP,
        side="PE",
        strike=23000,
        instrument_token="PE_23000",
        diff_at_signal=60.0,
        sum_ce=0.0,
        sum_pe=60.0,
        delta=60.0,
    )
    sig_b = inst._emit_signal(
        intent=SignalIntent.REVERSAL_FLIP,
        side="PE",
        strike=23000,
        instrument_token="PE_23000",
        diff_at_signal=60.0,
        sum_ce=0.0,
        sum_pe=60.0,
        delta=60.0,
    )
    assert sig_a == sig_b
    assert fake_redis_sync.xlen(K.STRATEGY_STREAM_SIGNALS) == 1
