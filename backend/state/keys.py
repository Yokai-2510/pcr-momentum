"""Canonical Redis key namespace.

Single source of truth: `docs/Schema.md` §1.

Every Redis key used by any engine MUST be constructed via a constant or
helper in this module. No engine should hand-build a key string.

Layout mirrors Schema.md's six top-level namespaces:
    1. system        — flags, health, lifecycle, scheduler
    2. user          — identity, credentials, auth, profile, capital
    3. market_data   — instruments, ticks, option chains, subscriptions, depth
    4. strategy      — registry, definitions, per-vessel state, signals
    5. orders        — positions, orders, broker state, PnL, allocator
    6. ui            — view payloads, pub/sub, streams

Index identifiers are lowercase: `nifty50`, `banknifty`, (future) `sensex`.
Strategy identifiers are lowercase snake_case with version suffix:
`bid_ask_imbalance_v1`.
"""

from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
IndexName = Literal["nifty50", "banknifty", "sensex"]
INDEXES: Final[tuple[IndexName, ...]] = ("nifty50", "banknifty")  # sensex reserved

# Trading-disabled-reason enum (Schema.md §1.1)
TradingDisabledReason = Literal[
    "none",
    "awaiting_credentials",
    "auth_invalid",
    "holiday",
    "manual_kill",
    "circuit_tripped",
]

# Vessel state machine (Strategy.md §5.5)
VesselState = Literal["FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"]

# Vessel lifecycle phase (Strategy.md §2)
VesselPhase = Literal["BOOT", "PRE_OPEN", "SETTLE", "LIVE", "DRAIN"]

# Auth health enum (Schema.md §1.1)
AuthHealth = Literal["valid", "invalid", "missing", "unknown"]


def _validate_index(index: str) -> None:
    if index not in INDEXES:
        raise ValueError(f"unknown index {index!r}; expected one of {INDEXES}")


def _validate_strategy_id(sid: str) -> None:
    if not sid or not sid.replace("_", "").isalnum():
        raise ValueError(f"invalid strategy_id {sid!r}; must be snake_case alnum")


# ===========================================================================
# 1. system:*
# ===========================================================================

# Flags
SYSTEM_FLAGS_READY: Final[str] = "system:flags:ready"
SYSTEM_FLAGS_TRADING_ACTIVE: Final[str] = "system:flags:trading_active"
SYSTEM_FLAGS_TRADING_DISABLED_REASON: Final[str] = "system:flags:trading_disabled_reason"
SYSTEM_FLAGS_MODE: Final[str] = "system:flags:mode"
SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED: Final[str] = "system:flags:daily_loss_circuit_triggered"
SYSTEM_FLAGS_INIT_FAILED: Final[str] = "system:flags:init_failed"

SYSTEM_FLAGS_DATA_PIPELINE_SUBSCRIBED: Final[str] = "system:flags:data_pipeline_subscribed"


def system_flag_engine_up(name: str) -> str:
    return f"system:flags:engine_up:{name}"


def system_flag_engine_exited(name: str) -> str:
    return f"system:flags:exited:{name}"


# Lifecycle
SYSTEM_LIFECYCLE_START_TS: Final[str] = "system:lifecycle:start_ts"
SYSTEM_LIFECYCLE_GIT_SHA: Final[str] = "system:lifecycle:git_sha"
SYSTEM_LIFECYCLE_LAST_SHUTDOWN_REASON: Final[str] = "system:lifecycle:last_shutdown_reason"

# Health
SYSTEM_HEALTH_SUMMARY: Final[str] = "system:health:summary"
SYSTEM_HEALTH_ENGINES: Final[str] = "system:health:engines"
SYSTEM_HEALTH_DEPENDENCIES: Final[str] = "system:health:dependencies"
SYSTEM_HEALTH_AUTH: Final[str] = "system:health:auth"
SYSTEM_HEALTH_HEARTBEATS: Final[str] = "system:health:heartbeats"
SYSTEM_HEALTH_ALERTS: Final[str] = "system:health:alerts"

# Scheduler
SYSTEM_SCHEDULER_TASKS: Final[str] = "system:scheduler:tasks"
SYSTEM_SCHEDULER_ACTIVE: Final[str] = "system:scheduler:active"
SYSTEM_SCHEDULER_TRADING_DAYS: Final[str] = "system:scheduler:market_calendar:trading_days"
SYSTEM_SCHEDULER_HOLIDAYS: Final[str] = "system:scheduler:market_calendar:holidays"
SYSTEM_SCHEDULER_SESSION: Final[str] = "system:scheduler:market_calendar:session"

# Streams + Pub/Sub
SYSTEM_STREAM_CONTROL: Final[str] = "system:stream:control"
SYSTEM_PUB_SYSTEM_EVENT: Final[str] = "system:pub:system_event"
SYSTEM_STREAM_SCHEDULER_EVENTS: Final[str] = "system:stream:scheduler_events"


# ===========================================================================
# 2. user:*
# ===========================================================================

USER_ACCOUNT_USERNAME: Final[str] = "user:account:username"
USER_ACCOUNT_JWT_SECRET: Final[str] = "user:account:jwt_secret"
USER_ACCOUNT_ROLE: Final[str] = "user:account:role"

USER_CREDENTIALS_UPSTOX: Final[str] = "user:credentials:upstox"

USER_AUTH_ACCESS_TOKEN: Final[str] = "user:auth:access_token"
USER_AUTH_LAST_REFRESH_TS: Final[str] = "user:auth:last_refresh_ts"

USER_PROFILE_ACCOUNT: Final[str] = "user:profile:account"
USER_CAPITAL_FUNDS: Final[str] = "user:capital:funds"
USER_CAPITAL_KILL_SWITCH: Final[str] = "user:capital:kill_switch"
USER_CAPITAL_STATIC_IPS: Final[str] = "user:capital:static_ips"


# ===========================================================================
# 3. market_data:*
# ===========================================================================

# Instruments
MARKET_DATA_INSTRUMENTS_MASTER: Final[str] = "market_data:instruments:master"
MARKET_DATA_INSTRUMENTS_LAST_REFRESH_TS: Final[str] = "market_data:instruments:last_refresh_ts"

# Subscriptions
MARKET_DATA_SUBSCRIPTIONS_SET: Final[str] = "market_data:subscriptions:set"
MARKET_DATA_SUBSCRIPTIONS_DESIRED: Final[str] = "market_data:subscriptions:desired"

# WS status
MARKET_DATA_WS_STATUS_MARKET: Final[str] = "market_data:ws_status:market_ws"
MARKET_DATA_WS_STATUS_PORTFOLIO: Final[str] = "market_data:ws_status:portfolio_ws"


def market_data_index_meta(index: str) -> str:
    _validate_index(index)
    return f"market_data:indexes:{index}:meta"


def market_data_index_spot(index: str) -> str:
    _validate_index(index)
    return f"market_data:indexes:{index}:spot"


def market_data_index_option_chain(index: str) -> str:
    """Per-strike payload (JSON STRING). New schema (Strategy.md §9.1):

        {strike: {"ce": {token, ltp, bid, ask,
                          bid_qty_l1..l5, ask_qty_l1..l5,
                          bid_price_l1..l5, ask_price_l1..l5,
                          total_bid_qty, total_ask_qty,
                          vol, oi, ts},
                  "pe": {...same shape...}}}
    """
    _validate_index(index)
    return f"market_data:indexes:{index}:option_chain"


def market_data_stream_tick(index: str) -> str:
    """Per-index tick stream (XADD MAXLEN ~50000)."""
    _validate_index(index)
    return f"market_data:stream:tick:{index}"


def market_data_pub_tick(token: str) -> str:
    """Pub/sub channel — data-pipeline PUBLISHes a notification per WS frame.

    Strategy vessels SUBSCRIBE to a list of these channels (one per basket
    token) to drive their tick-driven decision loop (Strategy.md §2.3).

    Payload is a single byte (`""`); subscribers read the latest state from
    Redis option_chain on wake-up. Pub/sub is fire-and-forget; no durability
    is needed.
    """
    return f"market_data:pub:tick:{token}"


# ===========================================================================
# 4. strategy:*
# ===========================================================================

# Top-level
STRATEGY_REGISTRY: Final[str] = "strategy:registry"
"""SET of `{strategy_id}:{instrument_id}` strings — the active vessels."""

STRATEGY_DEFINITIONS: Final[str] = "strategy:definitions"
"""HASH `{strategy_id: definition_json}` — synced from postgres at init."""

# Configs (shared across strategies)
STRATEGY_CONFIGS_EXECUTION: Final[str] = "strategy:configs:execution"
STRATEGY_CONFIGS_SESSION: Final[str] = "strategy:configs:session"
STRATEGY_CONFIGS_RISK: Final[str] = "strategy:configs:risk"


def strategy_config(sid: str) -> str:
    """Strategy-level config blob (JSON). See Strategy.md §10.1."""
    _validate_strategy_id(sid)
    return f"strategy:configs:strategies:{sid}"


def strategy_config_instrument(sid: str, index: str) -> str:
    """Instrument-level overrides for a strategy (JSON). See Strategy.md §10.2."""
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"strategy:configs:strategies:{sid}:instruments:{index}"


# ---------------------------------------------------------------------------
# Per-vessel state (one vessel = (strategy_id, instrument_id) pair)
# ---------------------------------------------------------------------------

def _vessel_prefix(sid: str, index: str) -> str:
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"strategy:{sid}:{index}"


def vessel_state(sid: str, index: str) -> str:
    """STRING — one of FLAT, IN_CE, IN_PE, COOLDOWN, HALTED."""
    return f"{_vessel_prefix(sid, index)}:state"


def vessel_phase(sid: str, index: str) -> str:
    """STRING — one of BOOT, PRE_OPEN, SETTLE, LIVE, DRAIN."""
    return f"{_vessel_prefix(sid, index)}:phase"


def vessel_phase_entered_ts(sid: str, index: str) -> str:
    """STRING (ms) — when current phase began."""
    return f"{_vessel_prefix(sid, index)}:phase_entered_ts"


def vessel_enabled(sid: str, index: str) -> str:
    """STRING `"true"` / `"false"` — operator master switch for this vessel."""
    return f"{_vessel_prefix(sid, index)}:enabled"


def vessel_basket(sid: str, index: str) -> str:
    """STRING (JSON) — `{"atm": int, "ce": [tok,...], "pe": [tok,...]}`."""
    return f"{_vessel_prefix(sid, index)}:basket"


def vessel_current_position_id(sid: str, index: str) -> str:
    """STRING — empty if FLAT, else the position_id of the open position."""
    return f"{_vessel_prefix(sid, index)}:current_position_id"


def vessel_cooldown_until_ts(sid: str, index: str) -> str:
    """STRING (ms) — 0 if not in cooldown."""
    return f"{_vessel_prefix(sid, index)}:cooldown_until_ts"


def vessel_cooldown_reason(sid: str, index: str) -> str:
    """STRING — last cooldown trigger (`post_sl`, `post_flip`, `manual`)."""
    return f"{_vessel_prefix(sid, index)}:cooldown_reason"


def vessel_counter_entries(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:counters:entries_today"


def vessel_counter_reversals(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:counters:reversals_today"


def vessel_counter_wins(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:counters:wins_today"


# ---------------------------------------------------------------------------
# Per-vessel live metrics (Strategy.md §11.1 — written every tick)
# ---------------------------------------------------------------------------

def vessel_metrics_per_strike(sid: str, index: str) -> str:
    """STRING (JSON) — map of {token: {imbalance, spread, wall_state, ...}}."""
    return f"{_vessel_prefix(sid, index)}:metrics:per_strike"


def vessel_metrics_cum_ce(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:metrics:cum_ce_imbalance"


def vessel_metrics_cum_pe(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:metrics:cum_pe_imbalance"


def vessel_metrics_net_pressure(sid: str, index: str) -> str:
    return f"{_vessel_prefix(sid, index)}:metrics:net_pressure"


def vessel_metrics_last_decision(sid: str, index: str) -> str:
    """STRING (JSON) — `{action, score, reason, ts_ms}` from last evaluation."""
    return f"{_vessel_prefix(sid, index)}:metrics:last_decision"


def vessel_metrics_last_decision_ts(sid: str, index: str) -> str:
    """STRING (ms) — used by health engine to detect silent vessels."""
    return f"{_vessel_prefix(sid, index)}:metrics:last_decision_ts"


# ---------------------------------------------------------------------------
# Signals (shared stream across all vessels — strategy_id is in the payload)
# ---------------------------------------------------------------------------

STRATEGY_SIGNALS_ACTIVE: Final[str] = "strategy:signals:active"
STRATEGY_SIGNALS_COUNTER: Final[str] = "strategy:signals:counter"
STRATEGY_STREAM_SIGNALS: Final[str] = "strategy:stream:signals"
STRATEGY_STREAM_REJECTED_SIGNALS: Final[str] = "strategy:stream:rejected_signals"


def strategy_signal(sig_id: str) -> str:
    return f"strategy:signals:{sig_id}"


# ===========================================================================
# 5. orders:*
# ===========================================================================

# Allocator — namespaced by (strategy_id, index) to enable per-vessel concurrency
ORDERS_ALLOCATOR_DEPLOYED: Final[str] = "orders:allocator:deployed"
"""STRING (float INR) — total capital reserved across ALL vessels."""

ORDERS_ALLOCATOR_OPEN_COUNT: Final[str] = "orders:allocator:open_count"
"""STRING (int) — total open positions across ALL vessels (global cap)."""


def orders_allocator_open_for_vessel(sid: str, index: str) -> str:
    """STRING (int) — open positions for a single vessel (per-vessel cap of 1)."""
    return f"orders:allocator:open:{sid}:{index}"


ORDERS_ALLOCATOR_OPEN_SYMBOLS: Final[str] = "orders:allocator:open_symbols"
"""SET of position_ids currently held — used for forensic debugging."""

# Positions
ORDERS_POSITIONS_OPEN: Final[str] = "orders:positions:open"
ORDERS_POSITIONS_CLOSED_TODAY: Final[str] = "orders:positions:closed_today"


def orders_position(pos_id: str) -> str:
    return f"orders:positions:{pos_id}"


def orders_positions_open_by_vessel(sid: str, index: str) -> str:
    """STRING — position_id of the currently-open position for this vessel, if any."""
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"orders:positions:open_by_vessel:{sid}:{index}"


def orders_order(order_id: str) -> str:
    return f"orders:orders:{order_id}"


def orders_status(pos_id: str) -> str:
    return f"orders:status:{pos_id}"


def orders_exit_pull(pos_id: str) -> str:
    """STRING flag — set by the order-exec dispatcher when a strategy-emitted
    EXIT signal arrives. Read and consumed by `exit_eval` as trigger #0
    (priority above SL/target/TSL). Format:

        f"STRATEGY_EXIT:<reason>"   e.g.  "STRATEGY_EXIT:continuation_failed"

    The worker DELs the key once the resulting exit has been processed
    through cleanup.
    """
    return f"orders:exit_pull:{pos_id}"


def orders_broker_pos(order_id: str) -> str:
    return f"orders:broker:pos:{order_id}"


ORDERS_BROKER_OPEN_ORDERS: Final[str] = "orders:broker:open_orders"

# PnL — total + per-strategy + per-vessel
ORDERS_PNL_REALIZED: Final[str] = "orders:pnl:realized"
ORDERS_PNL_UNREALIZED: Final[str] = "orders:pnl:unrealized"
ORDERS_PNL_DAY: Final[str] = "orders:pnl:day"


def orders_pnl_per_strategy(sid: str) -> str:
    _validate_strategy_id(sid)
    return f"orders:pnl:per_strategy:{sid}"


def orders_pnl_per_vessel(sid: str, index: str) -> str:
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"orders:pnl:per_vessel:{sid}:{index}"


# Buffered persistence (Background drains)
ORDERS_REPORTS_PENDING: Final[str] = "orders:reports:pending"

# Streams
ORDERS_STREAM_ORDER_EVENTS: Final[str] = "orders:stream:order_events"
ORDERS_STREAM_MANUAL_EXIT: Final[str] = "orders:stream:manual_exit"
ORDERS_STREAM_INTENT: Final[str] = "orders:intent:stream"


# ===========================================================================
# 6. ui:*
# ===========================================================================

UI_VIEW_DASHBOARD: Final[str] = "ui:views:dashboard"
UI_VIEW_POSITIONS_CLOSED_TODAY: Final[str] = "ui:views:positions_closed_today"
UI_VIEW_PNL: Final[str] = "ui:views:pnl"
UI_VIEW_CAPITAL: Final[str] = "ui:views:capital"
UI_VIEW_HEALTH: Final[str] = "ui:views:health"
UI_VIEW_CONFIGS: Final[str] = "ui:views:configs"
UI_VIEW_STRATEGIES: Final[str] = "ui:views:strategies"
"""Global view of all registered strategies + their per-vessel summary."""

UI_DIRTY: Final[str] = "ui:dirty"
UI_PUB_VIEW: Final[str] = "ui:pub:view"
UI_STREAM_HEALTH_ALERTS: Final[str] = "ui:stream:health_alerts"


def ui_view_vessel(sid: str, index: str) -> str:
    """Per-vessel live display payload (Strategy.md §11.2)."""
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"ui:views:vessels:{sid}:{index}"


def ui_view_position(index: str) -> str:
    _validate_index(index)
    return f"ui:views:position:{index}"


# ===========================================================================
# Heartbeat field names
# ===========================================================================
# Heartbeats live as HASH fields under SYSTEM_HEALTH_HEARTBEATS, not as
# standalone keys. Vessel heartbeats are computed dynamically:
#     f"strategy:{sid}:{index}"
# Static engine heartbeats are listed below.
HEARTBEAT_FIELDS_STATIC: Final[tuple[str, ...]] = (
    "init",
    "data_pipeline",
    "data_pipeline.subscription_manager",
    "order_exec",
    "background",
    "scheduler",
    "health",
    "api_gateway",
)


def heartbeat_field_vessel(sid: str, index: str) -> str:
    """HASH field name for a vessel heartbeat (under system:health:heartbeats)."""
    _validate_strategy_id(sid)
    _validate_index(index)
    return f"strategy:{sid}:{index}"


# ===========================================================================
# Back-compat shims — old per-index strategy keys
# ---------------------------------------------------------------------------
# The premium-diff strategy used `strategy:{index}:*` (no strategy_id).
# Order-exec, api_gateway, background, tests, and views all read/write through
# these helpers. Rather than refactor 35+ files in one cut, we re-export the
# old helpers here. They resolve to the new namespace under the active
# strategy_id (currently `bid_ask_imbalance_v1`).
#
# Once each consumer is migrated to be strategy-aware (Phase F.2 onwards),
# delete the corresponding shim here.
# ===========================================================================

DEFAULT_STRATEGY_ID: Final[str] = "bid_ask_imbalance_v1"


def strategy_enabled(index: str) -> str:
    return vessel_enabled(DEFAULT_STRATEGY_ID, index)


def strategy_state(index: str) -> str:
    return vessel_state(DEFAULT_STRATEGY_ID, index)


def strategy_basket(index: str) -> str:
    return vessel_basket(DEFAULT_STRATEGY_ID, index)


def strategy_pre_open(index: str) -> str:
    """DEPRECATED — premium-diff baseline, no longer used. Kept as a key for
    legacy callers that read/write it; safe to ignore the value."""
    _validate_index(index)
    return f"strategy:legacy:{index}:pre_open"


def strategy_live_sum_ce(index: str) -> str:
    """DEPRECATED — maps to cum_ce_imbalance metric on the new vessel."""
    return vessel_metrics_cum_ce(DEFAULT_STRATEGY_ID, index)


def strategy_live_sum_pe(index: str) -> str:
    return vessel_metrics_cum_pe(DEFAULT_STRATEGY_ID, index)


def strategy_live_delta(index: str) -> str:
    return vessel_metrics_net_pressure(DEFAULT_STRATEGY_ID, index)


def strategy_live_diffs(index: str) -> str:
    return vessel_metrics_per_strike(DEFAULT_STRATEGY_ID, index)


def strategy_live_last_decision_ts(index: str) -> str:
    return vessel_metrics_last_decision_ts(DEFAULT_STRATEGY_ID, index)


def strategy_current_position_id(index: str) -> str:
    return vessel_current_position_id(DEFAULT_STRATEGY_ID, index)


def strategy_cooldown_until_ts(index: str) -> str:
    return vessel_cooldown_until_ts(DEFAULT_STRATEGY_ID, index)


def strategy_cooldown_reason(index: str) -> str:
    return vessel_cooldown_reason(DEFAULT_STRATEGY_ID, index)


def strategy_counters_entries_today(index: str) -> str:
    return vessel_counter_entries(DEFAULT_STRATEGY_ID, index)


def strategy_counters_reversals_today(index: str) -> str:
    return vessel_counter_reversals(DEFAULT_STRATEGY_ID, index)


def strategy_counters_wins_today(index: str) -> str:
    return vessel_counter_wins(DEFAULT_STRATEGY_ID, index)


def strategy_config_index(index: str) -> str:
    """Old: per-index strategy config. New: per-vessel instrument config
    under the default strategy."""
    return strategy_config_instrument(DEFAULT_STRATEGY_ID, index)


# ΔPCR keys — DEPRECATED (no longer written, no longer consumed by strategy).
# Returning a stable legacy namespace so any leftover read returns empty.
def delta_pcr_baseline(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:baseline"


def delta_pcr_last_oi(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:last_oi"


def delta_pcr_interval(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:interval"


def delta_pcr_cumulative(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:cumulative"


def delta_pcr_history(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:history"


def delta_pcr_last_compute_ts(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:last_compute_ts"


def delta_pcr_mode(index: str) -> str:
    _validate_index(index)
    return f"strategy:legacy:{index}:delta_pcr:mode"


# Orders namespace back-compat
def orders_positions_open_by_index(index: str) -> str:
    """DEPRECATED — superseded by orders_positions_open_by_vessel. Kept as
    a legacy SET key for migrations that still write to it."""
    _validate_index(index)
    return f"orders:positions:open_by_index:{index}"


def orders_pnl_per_index(index: str) -> str:
    """Old: per-index PnL. Now mapped to per-vessel for the default strategy."""
    return orders_pnl_per_vessel(DEFAULT_STRATEGY_ID, index)


# UI back-compat
def ui_view_strategy(index: str) -> str:
    return ui_view_vessel(DEFAULT_STRATEGY_ID, index)


def ui_view_delta_pcr(index: str) -> str:
    """DEPRECATED — ΔPCR view; left in place to avoid breaking the frontend
    contract until that view is removed in Phase F.5."""
    _validate_index(index)
    return f"ui:views:legacy:delta_pcr:{index}"


# Strategy state enum alias (existing consumers import `StrategyState`).
StrategyState = VesselState
