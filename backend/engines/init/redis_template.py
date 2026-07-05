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
    # NOTE: deployed + open_count are HASHes. Fields are created on demand by
    # the allocator (HINCRBYFLOAT/HINCRBY) per vessel ("{sid}:{idx}"), per
    # strategy ("strategy:{sid}"), plus "total". Only "total" is seeded;
    # writing these as STRING causes WRONGTYPE and rejects every signal.
    K.ORDERS_ALLOCATOR_DEPLOYED: {
        "type": "hash",
        "value": {"total": "0"},
    },
    K.ORDERS_ALLOCATOR_OPEN_COUNT: {
        "type": "hash",
        "value": {"total": "0"},
    },
    K.ORDERS_ALLOCATOR_OPEN_SYMBOLS: {"type": "set_empty"},
    K.ORDERS_POSITIONS_OPEN: {"type": "set_empty"},
    K.ORDERS_POSITIONS_CLOSED_TODAY: {"type": "set_empty"},
    K.ORDERS_BROKER_OPEN_ORDERS: {"type": "set_empty"},
    K.ORDERS_PNL_REALIZED: {"type": "str", "value": "0"},
    K.ORDERS_PNL_UNREALIZED: {"type": "str", "value": "0"},
    K.ORDERS_PNL_DAY: {"type": "str", "value": "0"},
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
    ("open_gainer_loser_v1", "nifty50_stocks"),
    ("leaderboard_overtake_v1", "nifty50_stocks"),
    *[
        (sid, idx)
        for sid in (
            "oi_crossover_v1",
            "volume_diff_v1",
            "vwap_band_v1",
            "ltp_strength_v1",
        )
        for idx in ("nifty50", "banknifty", "sensex")
    ],
)


def _vessel_runtime_keys() -> dict[str, dict[str, Any]]:
    """Per-vessel runtime keys. Reset each session."""
    out: dict[str, dict[str, Any]] = {}
    for sid, idx in DEFAULT_VESSELS:
        out[K.vessel_enabled(sid, idx)] = {"type": "str", "value": "true"}
        out[K.vessel_state(sid, idx)] = {"type": "str", "value": "FLAT"}
        out[K.vessel_basket(sid, idx)] = {"type": "json", "value": {"atm": 0, "ce": [], "pe": []}}
        out[K.vessel_current_position_id(sid, idx)] = {"type": "str", "value": ""}
        out[K.vessel_cooldown_until_ts(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_cooldown_reason(sid, idx)] = {"type": "str", "value": ""}
        out[K.vessel_counter_entries(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_counter_reversals(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_counter_wins(sid, idx)] = {"type": "str", "value": "0"}
        out[K.vessel_metrics_latest(sid, idx)] = {"type": "json", "value": {}}
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

DEFAULT_STRATEGY_CONFIG_OPEN_GL: dict[str, Any] = {
    "name": "Open Gainer-Loser",
    "description": (
        "At market open (09:15 + 60s), buys the top NIFTY-50 gainer's CE and "
        "the top loser's PE via stock options. Direction confirmed by "
        "post-settlement premium bias vs the 09:10 snapshot."
    ),
    "capital_inr": 0,  # 0 = bounded only by the global capital envelope
    "max_parallel_positions": 2,  # gainer leg + loser leg
    "entry": {
        "window_sec": 60,  # only fire within 60s of open (restart-safe guard)
        "wait_for_fresh_tick": True,  # stock AND option must tick after open
    },
    "session": {"market_open": "09:15:00"},
    "leaderboard": {
        "exclude_circuit_limits": True,
        "circuit_limit_threshold_pct": 20,
        "exclude_suspended_stocks": True,
    },
    "direction_prediction": {
        # "basic" (GAINER->CE / LOSER->PE) | "post_settlement_bias" | "fixed"
        "mode": "post_settlement_bias",
        "fixed_side": "CE",
        "neutral_fallback": "category",  # original: NEUTRAL -> category map
        "smoothing_enabled": False,
        "smoothing_periods": 3,
        "post_settlement_bias": {
            "snapshot_time": "09:10:00",
            "bucket": "ITM",
            "strike_count": 3,
            "threshold_pct": 0.5,
        },
    },
    "instrument_selection": {"strike_reference": "ITM", "strike_offset": 2},
    "filters": {"premium": {"enabled": False, "min_ltp": 40, "max_ltp": 450}},
    "execution": {"signal_max_age_sec": 10},
}

DEFAULT_STRATEGY_CONFIG_OVERTAKE: dict[str, Any] = {
    "name": "Leaderboard Overtake",
    "description": (
        "Continuously ranks NIFTY-50 stocks by % change; a new rank-1 on the "
        "gainer/loser leaderboard buys that stock's CE/PE. Churn, threshold, "
        "bias and premium filters from the original rank-momentum system."
    ),
    "capital_inr": 0,
    "max_parallel_positions": 3,
    "session": {"market_open": "09:15:00", "no_entry_after": "15:00:00"},
    "entry": {"max_leaf_age_sec": 10},
    "leaderboard": {
        "min_stocks_for_ranking": 30,
        "exclude_circuit_limits": True,
        "circuit_limit_threshold_pct": 20,
        "exclude_suspended_stocks": True,
    },
    "entry_filters": {
        "discard_overtakes": {
            "enabled": True,
            "reject_pair_flip": True,
            "pair_cooldown_seconds": 0,
            "symbol_cooldown_seconds": 0,
        },
        "change_pct_threshold": {
            "enabled": False,
            "mode": "auto_premarket",
            "gainer_min_pct": 1.5,
            "loser_min_pct": 1.5,
        },
        "premium": {"enabled": False, "min_ltp": 40, "max_ltp": 450},
        "reentry_cooldown_sec": 900,
    },
    "direction_prediction": {
        "mode": "post_settlement_bias",
        "fixed_side": "CE",
        "neutral_fallback": "category",
        "reject_on_conflict": False,
        "smoothing_enabled": False,
        "smoothing_periods": 3,
        "post_settlement_bias": {
            "snapshot_time": "09:10:00",
            "bucket": "ITM",
            "strike_count": 3,
            "threshold_pct": 0.5,
        },
    },
    "instrument_selection": {"strike_reference": "ITM", "strike_offset": 2},
    "execution": {"signal_max_age_sec": 10},
}


def _universe_instrument_config(max_positions: int, max_entries: int) -> dict[str, Any]:
    """Exit profile maps the original exit_conditions: SL -20%, trailing-
    target ceiling 30%, TSL arm 10% / trail 3%, time exit 1200 s. Lot sizes
    are per-symbol (universe token_map); qty_lots = position_size_lots."""
    return {
        "instrument_id": "nifty50_stocks",
        "universe": True,
        "strike_step": 1,  # per-symbol steps live in the universe meta
        "lot_size": 1,  # per-symbol lot sizes resolved via token_map
        "qty_lots": 1,
        "max_positions_per_vessel": max_positions,
        "max_entries_per_day": max_entries,
        "max_reversals_per_day": 0,
        "sl_pct": 0.20,
        "target_pct": 0.30,
        "tsl_arm_pct": 0.10,
        "tsl_trail_pct": 0.03,
        "max_hold_sec": 1200,
        "post_sl_cooldown_sec": 0,  # per-symbol pacing lives in the strategy
        "post_reversal_cooldown_sec": 0,
    }


def _pcr_strategy_config(name: str, description: str, **indicator: Any) -> dict[str, Any]:
    """Each PCR strategy carries its OWN complete config — entry gates,
    indicator params AND the full exit stack. Nothing is shared."""
    return {
        "name": name,
        "description": description,
        "capital_inr": 0,
        "max_parallel_positions": 3,  # one leg per index
        "session": {"market_open": "09:15:00", "market_close": "15:30:00"},
        "entry": {
            "no_entry_after": "15:25:00",
            "max_entries_per_day": 20,
            "cooldown_minutes": 0,
            "max_leaf_age_sec": 10,
        },
        "instrument_selection": {"strike_offset": 0},  # ATM
        "universe": {"subscribe_range": 7, "hysteresis_sec": 5},
        "indicator": indicator,
        "exits": {
            "exit_on_counter_crossover": True,
            "sl_pct": 20.0,
            "target_pct": 0.0,  # 0 = disabled (crossover is the primary exit)
            "trailing_sl_enabled": True,
            "trailing_sl_trigger_pct": 10.0,
            "trailing_sl_step_pct": 3.0,
            "peak_trail_enabled": False,
            "peak_trail_pct": 80.0,
            "time_exit_enabled": False,
            "time_exit_at": "",
        },
        "execution": {"signal_max_age_sec": 10},
    }


PCR_STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    "oi_crossover_v1": _pcr_strategy_config(
        "OI Crossover",
        "PE-CE cumulative OI difference sign crossover over the ATM band.",
        band_strikes=5,
    ),
    "volume_diff_v1": _pcr_strategy_config(
        "Volume Diff",
        "ATM-band PE-CE volume difference sign crossover.",
        band_strikes=5,
    ),
    "vwap_band_v1": _pcr_strategy_config(
        "VWAP Band",
        "Spot vs session VWAP with a 0.05% band; fresh crossovers only.",
        band_strikes=5,
        band_pct=0.0005,
    ),
    "ltp_strength_v1": _pcr_strategy_config(
        "LTP Strength",
        "Strict 5-condition LTP option-strength regime (CE/PE session sums, "
        "rolling strength, VWAP confirmation).",
        band_strikes=5,
        rolling_minutes=5,
    ),
}

# Per-index sizing for the PCR strategies. The wide exit-profile values are
# a catastrophic backstop only — each strategy's OWN exit stack (above) is
# the real exit logic and fires first via EXIT signals.
_PCR_INDEX_SIZING: dict[str, dict[str, Any]] = {
    "nifty50": {"strike_step": 50, "lot_size": 75},
    "banknifty": {"strike_step": 100, "lot_size": 35},
    "sensex": {"strike_step": 100, "lot_size": 20},
}


def _pcr_instrument_config(idx: str) -> dict[str, Any]:
    sizing = _PCR_INDEX_SIZING[idx]
    return {
        "instrument_id": idx,
        "strike_step": sizing["strike_step"],
        "lot_size": sizing["lot_size"],
        "qty_lots": 1,
        "max_positions_per_vessel": 1,
        "max_entries_per_day": 20,
        "max_reversals_per_day": 0,
        "sl_pct": 0.50,
        "target_pct": 3.0,
        "tsl_arm_pct": 3.0,
        "tsl_trail_pct": 0.50,
        "max_hold_sec": 22500,
        "post_sl_cooldown_sec": 0,
        "post_reversal_cooldown_sec": 0,
    }


UNIVERSE_INSTRUMENT_CONFIGS: dict[str, dict[str, dict[str, Any]]] = {
    "open_gainer_loser_v1": {
        "nifty50_stocks": _universe_instrument_config(max_positions=2, max_entries=2)
    },
    "leaderboard_overtake_v1": {
        "nifty50_stocks": _universe_instrument_config(max_positions=3, max_entries=10)
    },
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
    pipe.set(
        K.strategy_config("open_gainer_loser_v1"),
        orjson.dumps(DEFAULT_STRATEGY_CONFIG_OPEN_GL),
        nx=True,
    )
    pipe.set(
        K.strategy_config("leaderboard_overtake_v1"),
        orjson.dumps(DEFAULT_STRATEGY_CONFIG_OVERTAKE),
        nx=True,
    )

    for pcr_sid, pcr_cfg in PCR_STRATEGY_CONFIGS.items():
        pipe.set(K.strategy_config(pcr_sid), orjson.dumps(pcr_cfg), nx=True)

    # Instrument-level configs — each strategy has its OWN per-instrument blob.
    instrument_configs_by_sid: dict[str, dict[str, dict[str, Any]]] = {
        "bid_ask_imbalance_v1": DEFAULT_INSTRUMENT_CONFIGS,
        **UNIVERSE_INSTRUMENT_CONFIGS,
        **{
            pcr_sid: {idx: _pcr_instrument_config(idx) for idx in _PCR_INDEX_SIZING}
            for pcr_sid in PCR_STRATEGY_CONFIGS
        },
    }
    for sid, idx in DEFAULT_VESSELS:
        cfg = instrument_configs_by_sid.get(sid, {}).get(idx)
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
        elif kind == "hash_empty" or kind == "set_empty":
            skipped += 1
            continue
        else:
            raise ValueError(f"unknown template type {kind!r} for key {key!r}")
        written += 1
    await pipe.execute()

    # Seed registry + default configs (idempotent — uses NX on configs).
    await seed_strategy_registry(redis)

    return {"deleted": deleted, "written": written, "skipped": skipped}
