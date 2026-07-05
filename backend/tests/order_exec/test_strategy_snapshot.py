"""Strategy decision snapshots flow Signal → stream → Position → report.

Each strategy defines its OWN snapshot shape (nested dicts, labels, gate
results — anything JSON-able). The infra carries it opaquely:
publisher (Action.snapshot / Action.metrics) → Signal.strategy_snapshot →
stream fields → dispatcher parse → Position.strategy_snapshot_entry →
ClosedPositionReport.signal_snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis
import fakeredis.aioredis
import orjson

from engines.order_exec import worker
from engines.order_exec.dispatcher import _signal_from_payload
from engines.strategy import publisher
from engines.strategy.strategies.base import Action, ActionKind
from state import keys as K

SID = "rank_momentum_v1"

# Deliberately rank-momentum-flavoured, nested, non-numeric — nothing like
# the bid/ask metrics schema.
SNAPSHOT = {
    "ranks": {"RELIANCE": 1, "TCS": 2, "HDFCBANK": 3},
    "gate_results": {"rank_gate": "pass", "volume_gate": "pass", "spread_gate": "skip"},
    "regime": "trending_up",
    "top_pick": {"symbol": "RELIANCE", "score": 8.7, "pct_change": 2.31},
}


async def test_publisher_carries_full_snapshot() -> None:
    server = fakeredis.FakeServer()
    redis_async = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    action = Action(
        ActionKind.ENTER,
        side="CE",
        strike=25000,
        instrument_token="NSE_FO|49520",
        qty_lots=1,
        metrics={"momentum_score": 8.7, "regime": "trending_up"},  # mixed types
        snapshot=SNAPSHOT,
    )
    sig_id = await publisher.emit_signal(
        redis_async, strategy_id=SID, instrument_id="nifty50", action=action
    )
    assert sig_id is not None

    # Persisted signal blob has the FULL nested snapshot.
    blob = orjson.loads(await redis_async.get(K.strategy_signal(sig_id)))
    assert blob["strategy_snapshot"] == SNAPSHOT
    # Numeric quick-access view only keeps numbers.
    assert blob["metrics_at_signal"] == {"momentum_score": 8.7}

    # Stream entry round-trips through the dispatcher parser.
    entries = await redis_async.xrange(K.STRATEGY_STREAM_SIGNALS)
    assert entries
    payload = {str(k): str(v) for k, v in entries[-1][1].items()}
    parsed = await _signal_from_payload(payload)
    assert parsed is not None
    assert parsed.strategy_snapshot == SNAPSHOT
    assert parsed.strategy_id == SID
    await redis_async.aclose()


async def test_publisher_falls_back_to_metrics_when_no_snapshot() -> None:
    server = fakeredis.FakeServer()
    redis_async = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    action = Action(
        ActionKind.ENTER,
        side="PE",
        strike=25100,
        instrument_token="NSE_FO|49521",
        qty_lots=1,
        metrics={"net_pressure": -0.8, "label": "PE_DOMINANT", "per_strike": {"25100": {"i": 1.4}}},
    )
    sig_id = await publisher.emit_signal(
        redis_async, strategy_id="bid_ask_imbalance_v1", instrument_id="nifty50", action=action
    )
    blob = orjson.loads(await redis_async.get(K.strategy_signal(sig_id)))
    # Whole metrics dict (including non-numeric + nested) becomes the snapshot.
    assert blob["strategy_snapshot"] == {
        "net_pressure": -0.8,
        "label": "PE_DOMINANT",
        "per_strike": {"25100": {"i": 1.4}},
    }
    await redis_async.aclose()


def test_position_round_trips_snapshot(fake_redis_sync: Any) -> None:
    """strategy_snapshot_entry survives the position-hash write/load cycle."""
    from state.schemas.position import ExitProfile, Position

    position = Position(
        pos_id="P-snap01",
        sig_id="sig-snap-1",
        index="nifty50",
        side="CE",
        strike=25000,
        instrument_token="NSE_FO|49520",
        qty=75,
        entry_order_id="OID-1",
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
        strategy_snapshot_entry=SNAPSHOT,
    )
    mapping = {
        k: worker._serialize_position_field(v)
        for k, v in position.model_dump(mode="json").items()
        if v is not None
    }
    fake_redis_sync.hset(K.orders_position(position.pos_id), mapping=mapping)

    loaded = worker._load_position_from_hash(fake_redis_sync, position.pos_id)
    assert loaded is not None
    assert loaded.strategy_snapshot_entry == SNAPSHOT
