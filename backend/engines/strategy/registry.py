"""
Strategy registry — discovers active vessels at engine boot.

Source of truth: `strategy:registry` SET in Redis (populated by the Init
engine from the `strategy_definitions` Postgres table).

Each entry is `"{strategy_id}:{instrument_id}"`. For each entry we resolve
the Strategy class via a static map below (one row per implemented strategy)
and load its config blobs from Redis.

Adding a new strategy is a 3-step change:
    1. Implement under `strategies/{name}/strategy.py`
    2. Add a row to `_STRATEGY_CLASSES` here
    3. Insert into the `strategy_definitions` Postgres table
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import orjson
from loguru import logger

from engines.strategy.strategies.base import Strategy, VesselContext
from engines.strategy.strategies.bid_ask_imbalance import (
    STRATEGY_ID as BID_ASK_ID,
    BidAskImbalanceStrategy,
)
from state import keys as K

# Static dispatch table: strategy_id -> Strategy class
_STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    BID_ASK_ID: BidAskImbalanceStrategy,
}


@dataclass(slots=True)
class VesselSpec:
    """One concrete vessel to spawn — (strategy_id, instrument_id) + configs."""

    strategy_id: str
    instrument_id: str
    strategy: Strategy
    context: VesselContext


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _read_json(redis_sync: Any, key: str) -> dict[str, Any]:
    raw = redis_sync.get(key)
    if not raw:
        return {}
    try:
        parsed = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    except orjson.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def discover_vessels(redis_sync: Any) -> list[VesselSpec]:
    """Read `strategy:registry` and resolve every entry into a VesselSpec.

    Entries with an unknown strategy_id are skipped with a warning (init
    should not have written them, but defensive parse).
    """
    log = logger.bind(engine="strategy", component="registry")
    raw_set = redis_sync.smembers(K.STRATEGY_REGISTRY)
    if not raw_set:
        log.warning("strategy:registry is empty; no vessels will run")
        return []

    specs: list[VesselSpec] = []
    for raw_entry in raw_set:
        entry = _decode(raw_entry)
        if ":" not in entry:
            log.warning(f"registry entry malformed: {entry!r}")
            continue
        sid, _, idx = entry.partition(":")
        if sid not in _STRATEGY_CLASSES:
            log.warning(f"registry entry references unknown strategy_id={sid!r}")
            continue

        strategy_cfg = _read_json(redis_sync, K.strategy_config(sid))
        instrument_cfg = _read_json(redis_sync, K.strategy_config_instrument(sid, idx))

        strategy = _STRATEGY_CLASSES[sid]()
        ctx = VesselContext(
            strategy_id=sid,
            instrument_id=idx,
            strategy_config=strategy_cfg,
            instrument_config=instrument_cfg,
        )
        specs.append(
            VesselSpec(strategy_id=sid, instrument_id=idx, strategy=strategy, context=ctx)
        )
        log.info(f"registry: discovered vessel {sid}:{idx}")

    return specs


def reload_vessel_config(redis_sync: Any, spec: VesselSpec) -> None:
    """Hot-reload config for a vessel. Strategy.md §10.3.

    Reads the latest config blobs and replaces them on the spec's context.
    """
    spec.context.strategy_config = _read_json(redis_sync, K.strategy_config(spec.strategy_id))
    spec.context.instrument_config = _read_json(
        redis_sync, K.strategy_config_instrument(spec.strategy_id, spec.instrument_id)
    )
