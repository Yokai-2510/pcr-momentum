"""
engines.init.redis_template — canonical Redis schema, applied at boot.

Mirrors `docs/Schema.md` §1 + `docs/Strategy.md` §9.1. Init walks `TEMPLATE`
and writes the default value for every key in the runtime namespaces
(`system:*`, `market_data:*`, `strategy:*` (except configs), `orders:*`,
`ui:*`).

The `user:*` and `strategy:configs:*` namespaces are populated by the
postgres hydrator (Init step 4) — NOT in `TEMPLATE`.

`flush_runtime_namespaces` deletes everything under the runtime prefixes
(except `strategy:configs:*` and `user:*`). It uses SCAN (never KEYS *) per
the hot-path discipline rule (HLD §9).
"""

from __future__ import annotations

from typing import Any, Final

import orjson
import redis.asyncio as _redis_async

from state import keys as K

# Namespaces that survive a runtime FLUSH
_PRESERVED_PREFIXES: Final[tuple[str, ...]] = (
    "user:",
    "strategy:configs:",
    "strategy:definitions",
    "strategy:registry",
)

# Namespaces actively scanned + cleared
_RUNTIME_PREFIXES: Final[tuple[str, ...]] = (
    "system:",
    "market_data:",
    "strategy:",  # except strategy:configs:* / definitions / registry (filtered below)
    "orders:",
    "ui:",
)


# Type tag → how to write
#   "str"        — SET key value
#   "json"       — SET key orjson(value)
#   "hash_empty" — DELETE then leave empty (hash auto-created on first HSET)
#   "set_empty"  — DELETE (sets created implicitly on SADD)

TEMPLATE: dict[str, dict[str, Any]] = {
    # ── system:flags ────────────────────────────────────────────────────
    K.SYSTEM_FLAGS_READY: {"type": "str", "value": "false"},
    K.SYSTEM_FLAGS_TRADING_ACTIVE: {"type": "str", "value": "false"},
    K.SYSTEM_FLAGS_TRADING_DISABLED_REASON: {"type": "str", "value": "none"},
    K.SYSTEM_FLAGS_MODE: {"type": "str", "value": "paper"},
    K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED: {"type": "str", "value": "false"},
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
    # ── orders: allocator + day-counters reset ──────────────────────────
    # NOTE: deployed + open_count are HASH per index (+ "total" field) — the
    # capital_allocator_check_and_reserve.lua expects HSET/HGET/HINCRBYFLOAT
    # operations on these keys. Writing them as STRING causes WRONGTYPE
    # errors during Lua execution and silently rejects every signal.
    K.ORDERS_ALLOCATOR_DEPLOYED: {
        "type": "hash",
        "value": {"nifty50": "0", "banknifty": "0", "total": "0"},
    },
    K.ORDERS_ALLOCATOR_OPEN_COUNT: {
        "type": "hash",
        "value": {"nifty50": "0", "banknifty": "0", "total": "0"},
    },
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
    K.UI_VIEW_STRATEGIES: {"type": "json", "value": {"vessels": []}},
    K.UI_DIRTY: {"type": "set_empty"},
}


# ────────────────────────────────────────────────────────────────────────
# Default vessel registry (persisted in postgres `strategy_definitions`,
# this is the in-memory fallback if init can't reach postgres yet).
# ────────────────────────────────────────────────────────────────────────
DEFAULT_VESSELS: tuple[tuple[str, str], ...] = (
    ("bid_ask_imbalance_v1", "nifty50"),
    ("bid_ask_imbalance_v1", "banknifty"),
)


def _vessel_runtime_keys() -> dict[str, dict[str, Any]]:
    """Per-vessel runtime keys. Reset each session."""
    out: dict[str, dict[str, Any]] = {}
    for sid, idx in DEFAULT_VESSELS:
        out[K.vessel_enabled(sid, idx)] = {"type": "str", "value": "true"}
        out[K.vessel_state(sid, idx)] = {"type": "str", "value": "FLAT"}
        out[K.vessel_phase(sid, idx)] = {"type": "str", "value": "BOOT"}
        out[K.vessel_phase_entered_ts(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_basket(sid, idx)] = {"type": "json", "value": {"atm": 0, "ce": [], "pe": []}}
        out[K.vessel_current_position_id(sid, idx)] = {"type": "str", "value": ""}
        out[K.vessel_cooldown_until_ts(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_cooldown_reason(sid, idx)] = {"type": "str", "value": ""}
        out[K.vessel_counter_entries(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_counter_reversals(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_counter_wins(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_metrics_per_strike(sid, idx)] = {"type": "json", "value": {}}
        out[K.vessel_metrics_net_pressure(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_metrics_cum_ce(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_metrics_cum_pe(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_metrics_last_decision(sid, idx)] = {"type": "json", "value": {}}
        out[K.vessel_metrics_last_decision_ts(sid, idx)] = {"type": "str", "value": "0"}
        out[K.ui_view_vessel(sid, idx)] = {"type": "json", "value": {}}
        out[K.orders_pnl_per_vessel(sid, idx)] = {"type": "str", "value": "0"}
        out[K.orders_allocator_open_for_vessel(sid, idx)] = {"type": "str", "value": "0"}
        # Per-strategy PnL aggregate (idempotent)
        out[K.orders_pnl_per_strategy(sid)] = {"type": "str", "value": "0"}
    return out


# Default strategy + instrument config blobs (Strategy.md §10).
# Init writes these only if the postgres hydrator did not provide them.
DEFAULT_STRATEGY_CONFIG_BID_ASK: dict[str, Any] = {
    "strategy_id": "bid_ask_imbalance_v1",
    "version": "1.0.0",
    "enabled": True,
    "thresholds": {
        "imbalance_strong_buy": 1.30,
        "imbalance_moderate_buy": 1.10,
        "imbalance_neutral_low": 0.90,
        "imbalance_moderate_sell": 0.70,
        "imbalance_continuation": 1.20,
        "net_pressure_entry_threshold": 0.50,
        "net_pressure_neutral_band": 0.20,
        "imbalance_drop_pct_for_reversal": 30.0,
        "ask_wall_qty_multiple": 5.0,
        "ltp_aggressor_tolerance_inr": 0.10,
    },
    "tick_speed": {"min_consecutive": 3, "window_ms": 1000},
    "buffer": {"ring_size": 50},
    "atm_shift": {"hysteresis_sec": 5},
    "reversal": {"lookback_ticks": 3, "suppress_sec": 30},
    "time_windows": [
        {"start": "09:15", "end": "09:30", "phase": "OPENING", "min_score": 8},
        {"start": "09:30", "end": "11:30", "phase": "PRIMARY", "min_score": 6},
        {"start": "11:30", "end": "13:30", "phase": "MID", "min_score": 7},
        {"start": "13:30", "end": "15:00", "phase": "CONTINUATION_ONLY", "min_score": 7},
        {"start": "15:00", "end": "15:30", "phase": "EXIT_ONLY", "min_score": None},
    ],
}

DEFAULT_INSTRUMENT_CONFIGS: dict[str, dict[str, Any]] = {
    "nifty50": {
        "instrument_id": "nifty50",
        "strike_step": 50,
        "lot_size": 75,
        "qty_lots": 1,
        "basket_size": 5,
        "expiry_basket_size": 7,
        "spread_good_inr": 0.50,
        "spread_moderate_inr": 1.00,
        "max_entries_per_day": 8,
        "max_reversals_per_day": 4,
        "sl_pct": 0.20,
        "target_pct": 0.50,
        "tsl_arm_pct": 0.15,
        "tsl_trail_pct": 0.05,
        "max_hold_sec": 1500,
        "post_sl_cooldown_sec": 60,
        "post_reversal_cooldown_sec": 90,
    },
    "banknifty": {
        "instrument_id": "banknifty",
        "strike_step": 100,
        "lot_size": 35,
        "qty_lots": 1,
        "basket_size": 5,
        "expiry_basket_size": 7,
        "spread_good_inr": 1.50,
        "spread_moderate_inr": 3.00,
        "max_entries_per_day": 8,
        "max_reversals_per_day": 4,
        "sl_pct": 0.20,
        "target_pct": 0.50,
        "tsl_arm_pct": 0.15,
        "tsl_trail_pct": 0.05,
        "max_hold_sec": 1500,
        "post_sl_cooldown_sec": 60,
        "post_reversal_cooldown_sec": 90,
    },
}


def full_template() -> dict[str, dict[str, Any]]:
    """Return TEMPLATE merged with per-vessel runtime keys."""
    out = dict(TEMPLATE)
    out.update(_vessel_runtime_keys())
    return out


async def flush_runtime_namespaces(redis: _redis_async.Redis) -> int:
    """SCAN-and-DEL every key under runtime namespaces, preserving user:* and
    strategy:configs:* / strategy:registry / strategy:definitions.

    Returns the number of keys deleted.
    """
    deleted = 0
    pipe = redis.pipeline(transaction=False)
    queued = 0
    for prefix in _RUNTIME_PREFIXES:
        async for raw in redis.scan_iter(match=f"{prefix}*", count=500):
            key = raw.decode() if isinstance(raw, bytes) else raw
            if any(key.startswith(p) or key == p.rstrip(":") for p in _PRESERVED_PREFIXES):
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


async def seed_strategy_registry(redis: _redis_async.Redis) -> None:
    """Populate `strategy:registry` SET + default config blobs if missing."""
    pipe = redis.pipeline(transaction=False)
    pipe.delete(K.STRATEGY_REGISTRY)
    for sid, idx in DEFAULT_VESSELS:
        pipe.sadd(K.STRATEGY_REGISTRY, f"{sid}:{idx}")

    # Strategy-level configs (one per strategy_id, not per vessel)
    pipe.set(
        K.strategy_config("bid_ask_imbalance_v1"),
        orjson.dumps(DEFAULT_STRATEGY_CONFIG_BID_ASK),
        nx=True,  # don't clobber operator-tuned configs
    )

    # Instrument-level configs
    for sid, idx in DEFAULT_VESSELS:
        cfg = DEFAULT_INSTRUMENT_CONFIGS.get(idx)
        if cfg is not None:
            pipe.set(
                K.strategy_config_instrument(sid, idx),
                orjson.dumps(cfg),
                nx=True,
            )

    await pipe.execute()


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
        elif kind == "hash":
            # Pre-populated HASH (e.g. allocator deployed/open_count which the
            # Lua expects as HASH from the first call).
            pipe.delete(key)
            pipe.hset(key, mapping=spec["value"])
        elif kind == "hash_empty":
            skipped += 1
            continue
        elif kind == "set_empty":
            skipped += 1
            continue
        else:
            raise ValueError(f"unknown template type {kind!r} for key {key!r}")
        written += 1
    await pipe.execute()

    # Seed registry + default configs (idempotent — uses NX on configs).
    await seed_strategy_registry(redis)

    return {"deleted": deleted, "written": written, "skipped": skipped}
