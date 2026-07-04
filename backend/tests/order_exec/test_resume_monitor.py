"""Restart-safety (Phase A5): resume_open_positions re-attaches monitors.

Seeds an open position (hash + open-set + persisted signal blob) the way a
crashed engine would have left it, then verifies boot-time resume enqueues
a ResumeMonitor work item with the position and rehydrated signal.
"""

from __future__ import annotations

import queue
from datetime import UTC, datetime
from typing import Any

import orjson

from engines.order_exec import worker
from state import keys as K
from state.schemas.position import ExitProfile, Position
from state.schemas.signal import Signal, SignalIntent

SID = "bid_ask_imbalance_v1"


def _position(pos_id: str = "P-resume01") -> Position:
    return Position(
        pos_id=pos_id,
        sig_id="sig-resume-1",
        index="nifty50",
        side="CE",
        strike=25000,
        instrument_token="NSE_FO|49520",
        qty=75,
        entry_order_id="OID-entry-1",
        entry_price=100.0,
        entry_ts=datetime.now(UTC),
        mode="paper",
        intent="FRESH_ENTRY",
        sl_level=80.0,
        target_level=150.0,
        tsl_arm_pct=0.15,
        tsl_trail_pct=0.05,
        peak_premium=100.0,
        current_premium=100.0,
        exit_profile=ExitProfile(
            sl_pct=0.2, target_pct=0.5, tsl_arm_pct=0.15, tsl_trail_pct=0.05, max_hold_sec=1500
        ),
        sum_ce_at_entry=0.0,
        sum_pe_at_entry=0.0,
        strategy_version=SID,
    )


def _signal() -> Signal:
    return Signal(
        sig_id="sig-resume-1",
        strategy_id=SID,
        instrument_id="nifty50",
        index="nifty50",
        side="CE",
        strike=25000,
        instrument_token="NSE_FO|49520",
        intent=SignalIntent.FRESH_ENTRY,
        qty_lots=1,
        decision_ts=int(datetime.now(UTC).timestamp() * 1000),
        ts=datetime.now(UTC),
    )


def _seed_open_position(redis: Any, position: Position, *, with_signal_blob: bool) -> None:
    mapping = {
        k: (orjson.dumps(v).decode() if isinstance(v, dict | list) else str(v))
        for k, v in position.model_dump(mode="json").items()
        if v is not None
    }
    redis.hset(K.orders_position(position.pos_id), mapping=mapping)
    redis.sadd(K.ORDERS_POSITIONS_OPEN, position.pos_id)
    if with_signal_blob:
        redis.set(
            K.strategy_signal(position.sig_id),
            orjson.dumps(_signal().model_dump(mode="json")),
        )


def test_resume_enqueues_open_position(fake_redis_sync: Any) -> None:
    position = _position()
    _seed_open_position(fake_redis_sync, position, with_signal_blob=True)

    q: queue.Queue = queue.Queue()
    resumed = worker.resume_open_positions(fake_redis_sync, q)
    assert resumed == 1

    item = q.get_nowait()
    assert isinstance(item, worker.ResumeMonitor)
    assert item.pos_id == position.pos_id
    assert item.position.entry_price == 100.0
    # Signal rehydrated from the persisted blob, full fidelity.
    assert item.signal.strategy_id == SID
    assert item.signal.sig_id == "sig-resume-1"


def test_resume_synthesizes_signal_when_blob_missing(fake_redis_sync: Any) -> None:
    position = _position(pos_id="P-resume02")
    _seed_open_position(fake_redis_sync, position, with_signal_blob=False)

    q: queue.Queue = queue.Queue()
    resumed = worker.resume_open_positions(fake_redis_sync, q)
    assert resumed == 1

    item = q.get_nowait()
    assert isinstance(item, worker.ResumeMonitor)
    # Synthesized from the position record; strategy_id recovered from
    # strategy_version.
    assert item.signal.strategy_id == SID
    assert item.signal.side == "CE"
    assert item.signal.instrument_token == "NSE_FO|49520"


def test_resume_skips_unloadable_and_empty(fake_redis_sync: Any) -> None:
    # Open-set references a pos_id with no hash behind it.
    fake_redis_sync.sadd(K.ORDERS_POSITIONS_OPEN, "P-ghost")
    q: queue.Queue = queue.Queue()
    assert worker.resume_open_positions(fake_redis_sync, q) == 0
    assert q.empty()
