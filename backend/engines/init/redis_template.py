"""
engines.init.redis_template — canonical Redis schema, applied at boot.

Hardcoded mirror of `docs/Schema.md` §1. Init walks `TEMPLATE` and writes
the default value for every key in the runtime namespaces (`system:*`,
`market_data:*`, `strategy:*`, `orders:*`, `ui:*`).

The `user:*` and `strategy:configs:*` namespaces are populated by the
postgres hydrator (Init step 4); they're NOT in `TEMPLATE`.

`flush_runtime_namespaces` deletes everything under `system:`, `market_data:`,
`strategy:` (except `strategy:configs:*`), `orders:`, and `ui:`. It uses
SCAN (never KEYS *) per the hot-path discipline rule (HLD §9).
"""

from __future__ import annotations

from typing import Any, Final

import orjson
import redis.asyncio as _redis_async

from state import keys as K

# Namespaces that survive a runtime FLUSH (set by hydrator + persisted creds)
_PRESERVED_PREFIXES: Final[tuple[str, ...]] = (
    "user:",
    "strategy:configs:",
)

# Namespaces actively scanned + cleared
_RUNTIME_PREFIXES: Final[tuple[str, ...]] = (
    "system:",
    "market_data:",
    "strategy:",  # except strategy:configs:* (filtered below)
    "orders:",
    "ui:",
)


# ────────────────────────────────────────────────────────────────────────
# TEMPLATE — one entry per Redis key in Schema.md §1.
# Values are written with Redis-native types: STRING via SET, HASH via
# HSET, SET via SADD, JSON via SET (orjson-serialized), STREAM is created
# implicitly by XADD elsewhere (we don't pre-create empty streams).
# ────────────────────────────────────────────────────────────────────────

# Type tag → how to write
#   "str"        — SET key value
#   "json"       — SET key orjson(value)
#   "hash_empty" — DELETE then leave empty (hash will be created on first HSET)
#   "set_empty"  — DELETE (sets created implicitly on SADD)
#   "skip"       — managed elsewhere (e.g. set_empty default but pre-populated elsewhere)

TEMPLATE: dict[str, dict[str, Any]] = {
    # ── system:flags ────────────────────────────────────────────────────
    K.SYSTEM_FLAGS_READY: {"type": "str", "value": "false"},
    K.SYSTEM_FLAGS_TRADING_ACTIVE: {"type": "str", "value": "false"},
    K.SYSTEM_FLAGS_TRADING_DISABLED_REASON: {"type": "str", "value": "none"},
    K.SYSTEM_FLAGS_MODE: {"type": "str", "value": "paper"},
    K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED: {"type": "str", "value": "false"},
    # init_failed is intentionally absent on success; only set on failure paths
    # ── system:lifecycle ────────────────────────────────────────────────
    K.SYSTEM_LIFECYCLE_START_TS: {"type": "str", "value": ""},
    K.SYSTEM_LIFECYCLE_GIT_SHA: {"type": "str", "value": ""},
    K.SYSTEM_LIFECYCLE_LAST_SHUTDOWN_REASON: {"type": "str", "value": ""},
    # ── system:health ────────────────────────────────────────────────
    K.SYSTEM_HEALTH_SUMMARY: {"type": "hash_empty"},
    K.SYSTEM_HEALTH_AUTH: {"type": "str", "value": "unknown"},
    K.SYSTEM_HEALTH_ENGINES: {"type": "hash_empty"},
    K.SYSTEM_HEALTH_DEPENDENCIES: {"type": "hash_empty"},
    K.SYSTEM_HEALTH_HEARTBEATS: {"type": "hash_empty"},
    # ── system:scheduler ────────────────────────────────────────────────
    K.SYSTEM_SCHEDULER_TASKS: {"type": "hash_empty"},
    K.SYSTEM_SCHEDULER_ACTIVE: {"type": "set_empty"},
    K.SYSTEM_SCHEDULER_TRADING_DAYS: {"type": "set_empty"},
    K.SYSTEM_SCHEDULER_HOLIDAYS: {"type": "set_empty"},
    K.SYSTEM_SCHEDULER_SESSION: {"type": "hash_empty"},
    # ── market_data ─────────────────────────────────────────────────────
    K.MARKET_DATA_INSTRUMENTS_MASTER: {"type": "hash_empty"},
    K.MARKET_DATA_INSTRUMENTS_LAST_REFRESH_TS: {"type": "str", "value": ""},
    K.MARKET_DATA_SUBSCRIPTIONS_SET: {"type": "set_empty"},
    K.MARKET_DATA_SUBSCRIPTIONS_DESIRED: {"type": "set_empty"},
    K.MARKET_DATA_WS_STATUS_MARKET: {"type": "hash_empty"},
    K.MARKET_DATA_WS_STATUS_PORTFOLIO: {"type": "hash_empty"},
    # ── strategy: per-index runtime (configs come from hydrator) ────────
    # state, enabled, basket, pre_open, counters reset each day
    # ── orders: allocator + day-counters reset ──────────────────────────
    K.ORDERS_ALLOCATOR_DEPLOYED: {"type": "str", "value": "0"},
    K.ORDERS_ALLOCATOR_OPEN_COUNT: {"type": "hash_empty"},
    K.ORDERS_ALLOCATOR_OPEN_SYMBOLS: {"type": "set_empty"},
    K.ORDERS_POSITIONS_OPEN: {"type": "set_empty"},
    K.ORDERS_POSITIONS_CLOSED_TODAY: {"type": "set_empty"},
    K.ORDERS_BROKER_OPEN_ORDERS: {"type": "set_empty"},
    K.ORDERS_PNL_REALIZED: {"type": "str", "value": "0"},
    K.ORDERS_PNL_UNREALIZED: {"type": "str", "value": "0"},
    K.ORDERS_PNL_DAY: {"type": "str", "value": "0"},
    # ── strategy:signals ────────────────────────────────────────────────
    K.STRATEGY_SIGNALS_ACTIVE: {"type": "set_empty"},
    K.STRATEGY_SIGNALS_COUNTER: {"type": "str", "value": "0"},
    # ── ui:views ────────────────────────────────────────────────────────
    K.UI_VIEW_DASHBOARD: {"type": "json", "value": {}},
    K.UI_VIEW_POSITIONS_CLOSED_TODAY: {"type": "json", "value": []},
    K.UI_VIEW_PNL: {"type": "json", "value": {"realized": 0, "unrealized": 0, "day": 0}},
    K.UI_VIEW_CAPITAL: {"type": "json", "value": {}},
    K.UI_VIEW_HEALTH: {"type": "json", "value": {"summary": "OK", "engines": {}}},
    K.UI_VIEW_CONFIGS: {"type": "json", "value": {}},
    K.UI_DIRTY: {"type": "set_empty"},
}


def _per_index_runtime_keys() -> dict[str, dict[str, Any]]:
    """Per-index strategy + ΔPCR + orders keys. Built dynamically from K.INDEXES."""
    out: dict[str, dict[str, Any]] = {}
    for idx in K.INDEXES:
        out[K.strategy_enabled(idx)] = {"type": "str", "value": "true"}
        out[K.strategy_state(idx)] = {"type": "str", "value": "FLAT"}
        out[K.strategy_cooldown_until_ts(idx)] = {"type": "str", "value": "0"}
        out[K.strategy_cooldown_reason(idx)] = {"type": "str", "value": ""}
        out[K.strategy_counters_entries_today(idx)] = {"type": "str", "value": "0"}
        out[K.strategy_counters_reversals_today(idx)] = {"type": "str", "value": "0"}
        out[K.strategy_counters_wins_today(idx)] = {"type": "str", "value": "0"}
        out[K.strategy_current_position_id(idx)] = {"type": "str", "value": ""}
        # ΔPCR per index
        out[K.delta_pcr_cumulative(idx)] = {"type": "str", "value": "1.0"}
        out[K.delta_pcr_last_compute_ts(idx)] = {"type": "str", "value": "0"}
        out[K.delta_pcr_mode(idx)] = {"type": "str", "value": "1"}
        out[K.delta_pcr_history(idx)] = {"type": "set_empty"}
        # Per-index orders
        out[K.orders_positions_open_by_index(idx)] = {"type": "set_empty"}
        out[K.orders_pnl_per_index(idx)] = {"type": "str", "value": "0"}
        # UI views per index
        out[K.ui_view_strategy(idx)] = {"type": "json", "value": {}}
        out[K.ui_view_position(idx)] = {"type": "json", "value": {}}
        out[K.ui_view_delta_pcr(idx)] = {"type": "json", "value": {}}
    return out


def full_template() -> dict[str, dict[str, Any]]:
    """Return TEMPLATE merged with per-index runtime keys."""
    out = dict(TEMPLATE)
    out.update(_per_index_runtime_keys())
    return out


async def flush_runtime_namespaces(redis: _redis_async.Redis) -> int:
    """SCAN-and-DEL every key under runtime namespaces, preserving user:* and
    strategy:configs:*.

    Returns the number of keys deleted.
    """
    deleted = 0
    pipe = redis.pipeline(transaction=False)
    queued = 0
    for prefix in _RUNTIME_PREFIXES:
        async for raw in redis.scan_iter(match=f"{prefix}*", count=500):
            key = raw.decode() if isinstance(raw, bytes) else raw
            if any(key.startswith(p) for p in _PRESERVED_PREFIXES):
                continue
            pipe.delete(key)
            queued += 1
            if queued >= 500:
                results = await pipe.execute()
                deleted += sum(1 for r in results if r)
                pipe = redis.pipeline(transaction=False)
                queued = 0
    if queued:
        results = await pipe.execute()
        deleted += sum(1 for r in results if r)
    return deleted


async def apply(redis: _redis_async.Redis, flush_runtime: bool = True) -> dict[str, int]:
    """Apply the canonical template to Redis.

    Args:
        redis: async Redis client.
        flush_runtime: if True, runs `flush_runtime_namespaces` first.

    Returns:
        Counters: {"deleted": N, "written": M, "skipped": K}.
    """
    deleted = 0
    if flush_runtime:
        deleted = await flush_runtime_namespaces(redis)

    written = 0
    skipped = 0
    template = full_template()
    pipe = redis.pipeline(transaction=False)
    for key, spec in template.items():
        kind = spec["type"]
        if kind == "str":
            pipe.set(key, spec["value"])
        elif kind == "json":
            pipe.set(key, orjson.dumps(spec["value"]))
        elif kind == "hash_empty":
            # Hashes are auto-created on first HSET; we just ensure stale
            # values from before the flush are gone (already handled by
            # flush_runtime_namespaces above).
            skipped += 1
            continue
        elif kind == "set_empty":
            skipped += 1
            continue
        else:
            raise ValueError(f"unknown template type {kind!r} for key {key!r}")
        written += 1
    await pipe.execute()
    return {"deleted": deleted, "written": written, "skipped": skipped}
