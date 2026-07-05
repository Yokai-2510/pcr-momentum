"""Per-strategy execution policy overlay (exec_policy.read_execution_policy).

Every order-path knob (buffer_inr, open_timeout_sec, chase_ceiling_inr,
spread_skip_pct, signal_max_age_sec, ...) resolves as:
built-in default < global execution config < strategy's own `execution` block.
"""

from __future__ import annotations

from typing import Any

import orjson

from engines.order_exec import exec_policy
from state import keys as K

SID = "bid_ask_imbalance_v1"


def _seed(redis: Any, *, global_cfg: dict, strategy_exec: dict | None = None) -> None:
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps(global_cfg))
    if strategy_exec is not None:
        redis.set(K.strategy_config(SID), orjson.dumps({"execution": strategy_exec}))


def test_global_only(fake_redis_sync: Any) -> None:
    _seed(fake_redis_sync, global_cfg={"buffer_inr": 2, "open_timeout_sec": 8})
    policy = exec_policy.read_execution_policy(fake_redis_sync, SID)
    assert policy["buffer_inr"] == 2
    assert policy["open_timeout_sec"] == 8


def test_strategy_overrides_win(fake_redis_sync: Any) -> None:
    _seed(
        fake_redis_sync,
        global_cfg={"buffer_inr": 2, "open_timeout_sec": 8, "chase_ceiling_inr": 15},
        strategy_exec={"open_timeout_sec": 20, "chase_ceiling_inr": 25},
    )
    policy = exec_policy.read_execution_policy(fake_redis_sync, SID)
    # overridden
    assert policy["open_timeout_sec"] == 20
    assert policy["chase_ceiling_inr"] == 25
    # inherited
    assert policy["buffer_inr"] == 2


def test_other_strategy_unaffected(fake_redis_sync: Any) -> None:
    _seed(
        fake_redis_sync,
        global_cfg={"open_timeout_sec": 8},
        strategy_exec={"open_timeout_sec": 20},
    )
    policy = exec_policy.read_execution_policy(fake_redis_sync, "some_other_strategy")
    assert policy["open_timeout_sec"] == 8


def test_no_strategy_id_returns_global(fake_redis_sync: Any) -> None:
    _seed(fake_redis_sync, global_cfg={"buffer_inr": 3})
    policy = exec_policy.read_execution_policy(fake_redis_sync, None)
    assert policy["buffer_inr"] == 3


def test_paper_entry_uses_strategy_buffer(fake_redis_sync: Any) -> None:
    """End-to-end: the paper fill price honours the strategy's own buffer_inr."""
    from datetime import UTC, datetime

    from engines.order_exec import entry as entry_mod
    from state.schemas.signal import Signal, SignalIntent

    _seed(
        fake_redis_sync,
        global_cfg={"buffer_inr": 2},
        strategy_exec={"buffer_inr": 5},
    )
    chain = {
        "23000": {
            "ce": {
                "token": "NSE_FO|49520",
                "ltp": 100.0,
                "bid": 99.5,
                "ask": 100.5,
                "bid_qty": 1500,
                "ask_qty": 1500,
                "vol": 0,
                "oi": 0,
                "ts": 1,
            },
            "pe": None,
        }
    }
    fake_redis_sync.set(K.market_data_index_option_chain("nifty50"), orjson.dumps(chain))
    sig = Signal(
        sig_id="s1",
        strategy_id=SID,
        instrument_id="nifty50",
        index="nifty50",
        side="CE",
        strike=23000,
        instrument_token="NSE_FO|49520",
        intent=SignalIntent.FRESH_ENTRY,
        qty_lots=1,
        decision_ts=int(datetime.now(UTC).timestamp() * 1000),
        ts=datetime.now(UTC),
    )
    result = entry_mod.submit_and_monitor_paper(fake_redis_sync, sig, "P-x", lot_size=75)
    # ask 100.5 + strategy buffer 5 (NOT global 2)
    assert result.avg_fill_price == 105.5
