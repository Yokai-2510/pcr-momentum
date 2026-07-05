"""
engines.order_exec.exec_policy — per-strategy execution policy resolution.

Execution knobs (buffer_inr, open_timeout_sec, max_retries, chase_ceiling_inr,
partial_grace_sec, spread_skip_pct, signal_max_age_sec, eod_buffer_inr, ...)
are grounded in the instrument's microstructure, but strategies differ in
URGENCY: a momentum flip must fill now and can pay up; a bootstrap entry can
be patient with a tight limit.

Resolution order (later wins):
    1. built-in defaults (each call site's `or <default>` fallback)
    2. global `strategy:configs:execution` blob
    3. the strategy's OWN `execution` section inside its config blob
       (`strategy:configs:strategies:{sid}` -> {"execution": {...}})

Every order-path config read goes through `read_execution_policy` so a
strategy can override ANY execution key without touching global config.
"""

from __future__ import annotations

from typing import Any

import orjson
import redis as _redis_sync

from state import keys as K


def _read_json_dict(redis_sync: _redis_sync.Redis, key: str) -> dict[str, Any]:
    raw = redis_sync.get(key)
    if not raw:
        return {}
    try:
        parsed = orjson.loads(raw if isinstance(raw, bytes) else str(raw).encode())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_execution_policy(redis_sync: _redis_sync.Redis, strategy_id: str | None) -> dict[str, Any]:
    """Global execution config overlaid with the strategy's own overrides."""
    policy = _read_json_dict(redis_sync, K.STRATEGY_CONFIGS_EXECUTION)
    if strategy_id:
        strat_cfg = _read_json_dict(redis_sync, K.strategy_config(strategy_id))
        override = strat_cfg.get("execution")
        if isinstance(override, dict):
            policy = {**policy, **override}
    return policy
