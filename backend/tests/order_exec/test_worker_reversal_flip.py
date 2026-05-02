"""Bug-2 regression: REVERSAL_FLIP closes the prior leg atomically.

We exercise `_close_existing_position_for_flip` directly with a hydrated
prior position so we don't have to drive the full e2e exit cascade twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import orjson
import pytest

from engines.order_exec import worker
from state import keys as K
from state.schemas.position import ExitProfile, Position


def _make_prior_position(pos_id: str = "P-PRIOR-1") -> Position:
    return Position(
        pos_id=pos_id,
        sig_id="sig-prior-1",
        index="nifty50",
        side="CE",
        strike=23000,
        instrument_token="NSE_FO|49520",
        qty=75,
        entry_order_id="O-PRIOR",
        exit_order_id=None,
        entry_price=100.0,
        entry_ts=datetime.now(UTC),
        exit_price=None,
        exit_ts=None,
        mode="paper",
        intent="FRESH_ENTRY",
        sl_level=80.0,
        target_level=150.0,
        tsl_armed=False,
        tsl_arm_pct=0.15,
        tsl_trail_pct=0.05,
        tsl_level=None,
        peak_premium=110.0,
        current_premium=110.0,
        pnl=0.0,
        pnl_pct=0.0,
        holding_seconds=0,
        exit_profile=ExitProfile(
            sl_pct=0.20,
            target_pct=0.50,
            tsl_arm_pct=0.15,
            tsl_trail_pct=0.05,
            max_hold_sec=1500,
        ),
        sum_ce_at_entry=20.0,
        sum_pe_at_entry=0.0,
        delta_pcr_at_entry=None,
        strategy_version="t",
    )


def _seed_for_flip(redis: Any, prior: Position) -> None:
    """Persist the prior position into Redis so the worker can hydrate it."""
    redis.set(K.SYSTEM_FLAGS_MODE, "paper")
    chain = {
        "23000": {
            "ce": {
                "token": prior.instrument_token,
                "ltp": 110.0, "bid": 109.5, "ask": 110.5,
                "bid_qty": 1500, "ask_qty": 1500, "vol": 0, "oi": 0, "ts": 1,
            },
            "pe": None,
        }
    }
    redis.set(K.market_data_index_option_chain(prior.index), orjson.dumps(chain))
    redis.set(K.STRATEGY_CONFIGS_EXECUTION, orjson.dumps({
        "buffer_inr": 2.0, "eod_buffer_inr": 5.0,
        "liquidity_exit_suppress_after": "15:00",
    }))
    # Persist position HASH + membership.
    redis.hset(
        K.orders_position(prior.pos_id),
        mapping={
            k: (
                orjson.dumps(v).decode() if isinstance(v, dict | list)
                else (v.isoformat() if isinstance(v, datetime) else str(v))
            )
            for k, v in prior.model_dump(mode="json").items()
            if v is not None
        },
    )
    redis.sadd(K.ORDERS_POSITIONS_OPEN, prior.pos_id)
    redis.sadd(K.orders_positions_open_by_index(prior.index), prior.pos_id)
    redis.set(K.strategy_current_position_id(prior.index), prior.pos_id)


@pytest.fixture
def patched_cleanup_lua(monkeypatch: pytest.MonkeyPatch) -> None:
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
        pipe.srem(K.ORDERS_POSITIONS_OPEN, pos_id)
        pipe.srem(K.orders_positions_open_by_index(index), pos_id)
        pipe.sadd(K.ORDERS_POSITIONS_CLOSED_TODAY, pos_id)
        pipe.delete(K.strategy_current_position_id(index))
        pipe.execute()
        return 1

    monkeypatch.setattr(cleanup_mod, "cleanup", _stub_cleanup)


def test_load_position_from_hash_roundtrips(fake_redis_sync: Any) -> None:
    prior = _make_prior_position()
    _seed_for_flip(fake_redis_sync, prior)

    hydrated = worker._load_position_from_hash(fake_redis_sync, prior.pos_id)
    assert hydrated is not None
    assert hydrated.pos_id == prior.pos_id
    assert hydrated.qty == prior.qty
    assert hydrated.entry_price == prior.entry_price


def test_close_existing_position_for_flip_closes_prior(
    fake_redis_sync: Any, patched_cleanup_lua: None
) -> None:
    """REVERSAL_FLIP must close the existing position before the new entry."""
    prior = _make_prior_position()
    _seed_for_flip(fake_redis_sync, prior)

    from loguru import logger
    log = logger.bind(engine="test")
    ok = worker._close_existing_position_for_flip(
        fake_redis_sync, prior.index, mode="paper", access_token="", log=log,
    )
    assert ok is True

    # Prior position is gone from open set + present in closed_today.
    assert not fake_redis_sync.sismember(K.ORDERS_POSITIONS_OPEN, prior.pos_id)
    assert fake_redis_sync.sismember(K.ORDERS_POSITIONS_CLOSED_TODAY, prior.pos_id)
    # current_position_id pointer wiped.
    assert fake_redis_sync.get(K.strategy_current_position_id(prior.index)) in (None, "")
    # Report buffered for Background to drain.
    assert fake_redis_sync.llen(K.ORDERS_REPORTS_PENDING) >= 1


def test_close_existing_no_op_when_no_current_position(fake_redis_sync: Any) -> None:
    from loguru import logger
    log = logger.bind(engine="test")
    ok = worker._close_existing_position_for_flip(
        fake_redis_sync, "nifty50", mode="paper", access_token="", log=log,
    )
    assert ok is True  # nothing to close → still success
