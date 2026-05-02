"""Canonical Redis key namespace.

Single source of truth: `docs/Schema.md` §1.

Every Redis key used by any engine MUST be constructed via a constant or
helper in this module. No engine should hand-build a key string. This is
enforced socially — any new key requires a Schema.md edit + a new helper
here in the same PR.

Layout mirrors Schema.md's six top-level namespaces:
    1. system        — flags, health, lifecycle, scheduler
    2. user          — identity, credentials, auth, profile, capital
    3. market_data   — instruments, ticks, option chains, subscriptions
    4. strategy      — per-index state, configs, signals, ΔPCR
    5. orders        — positions, orders, broker state, PnL, allocator
    6. ui            — view payloads, pub/sub, streams

Index identifiers are lowercase: `nifty50`, `banknifty`.
"""

from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
IndexName = Literal["nifty50", "banknifty"]
INDEXES: Final[tuple[IndexName, ...]] = ("nifty50", "banknifty")

# Trading-disabled-reason enum (Schema.md §1.1)
TradingDisabledReason = Literal[
    "none",
    "awaiting_credentials",
    "auth_invalid",
    "holiday",
    "manual_kill",
    "circuit_tripped",
]

# Strategy state enum (Strategy.md §3 / Schema.md §1.4)
StrategyState = Literal["FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"]

# Auth health enum (Schema.md §1.1)
AuthHealth = Literal["valid", "invalid", "missing", "unknown"]


def _validate_index(index: str) -> None:
    if index not in INDEXES:
        raise ValueError(f"unknown index {index!r}; expected one of {INDEXES}")


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


# ===========================================================================
# 2. user:*
# ===========================================================================

# Identity
USER_ACCOUNT_USERNAME: Final[str] = "user:account:username"
USER_ACCOUNT_JWT_SECRET: Final[str] = "user:account:jwt_secret"
USER_ACCOUNT_ROLE: Final[str] = "user:account:role"

# Credentials
USER_CREDENTIALS_UPSTOX: Final[str] = "user:credentials:upstox"

# Auth
USER_AUTH_ACCESS_TOKEN: Final[str] = "user:auth:access_token"
USER_AUTH_LAST_REFRESH_TS: Final[str] = "user:auth:last_refresh_ts"

# Profile + Capital
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
    _validate_index(index)
    return f"market_data:indexes:{index}:option_chain"


def market_data_stream_tick(index: str) -> str:
    _validate_index(index)
    return f"market_data:stream:tick:{index}"


# ===========================================================================
# 4. strategy:*
# ===========================================================================

# Configs
STRATEGY_CONFIGS_EXECUTION: Final[str] = "strategy:configs:execution"
STRATEGY_CONFIGS_SESSION: Final[str] = "strategy:configs:session"
STRATEGY_CONFIGS_RISK: Final[str] = "strategy:configs:risk"


def strategy_config_index(index: str) -> str:
    _validate_index(index)
    return f"strategy:configs:indexes:{index}"


# Per-index state
def strategy_enabled(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:enabled"


def strategy_state(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:state"


def strategy_basket(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:basket"


def strategy_pre_open(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:pre_open"


def strategy_live_sum_ce(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:live:sum_ce"


def strategy_live_sum_pe(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:live:sum_pe"


def strategy_live_delta(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:live:delta"


def strategy_live_diffs(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:live:diffs"


def strategy_live_last_decision_ts(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:live:last_decision_ts"


def strategy_current_position_id(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:current_position_id"


def strategy_cooldown_until_ts(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:cooldown_until_ts"


def strategy_cooldown_reason(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:cooldown_reason"


def strategy_counters_entries_today(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:counters:entries_today"


def strategy_counters_reversals_today(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:counters:reversals_today"


def strategy_counters_wins_today(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:counters:wins_today"


# ΔPCR per index
def delta_pcr_baseline(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:baseline"


def delta_pcr_last_oi(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:last_oi"


def delta_pcr_interval(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:interval"


def delta_pcr_cumulative(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:cumulative"


def delta_pcr_history(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:history"


def delta_pcr_last_compute_ts(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:last_compute_ts"


def delta_pcr_mode(index: str) -> str:
    _validate_index(index)
    return f"strategy:{index}:delta_pcr:mode"


# Signals
STRATEGY_SIGNALS_ACTIVE: Final[str] = "strategy:signals:active"
STRATEGY_SIGNALS_COUNTER: Final[str] = "strategy:signals:counter"
STRATEGY_STREAM_SIGNALS: Final[str] = "strategy:stream:signals"
STRATEGY_STREAM_REJECTED_SIGNALS: Final[str] = "strategy:stream:rejected_signals"


def strategy_signal(sig_id: str) -> str:
    return f"strategy:signals:{sig_id}"


# ===========================================================================
# 5. orders:*
# ===========================================================================

# Allocator
ORDERS_ALLOCATOR_DEPLOYED: Final[str] = "orders:allocator:deployed"
ORDERS_ALLOCATOR_OPEN_COUNT: Final[str] = "orders:allocator:open_count"
ORDERS_ALLOCATOR_OPEN_SYMBOLS: Final[str] = "orders:allocator:open_symbols"

# Positions
ORDERS_POSITIONS_OPEN: Final[str] = "orders:positions:open"
ORDERS_POSITIONS_CLOSED_TODAY: Final[str] = "orders:positions:closed_today"


def orders_position(pos_id: str) -> str:
    return f"orders:positions:{pos_id}"


def orders_positions_open_by_index(index: str) -> str:
    _validate_index(index)
    return f"orders:positions:open_by_index:{index}"


def orders_order(order_id: str) -> str:
    return f"orders:orders:{order_id}"


def orders_status(pos_id: str) -> str:
    return f"orders:status:{pos_id}"


def orders_broker_pos(order_id: str) -> str:
    return f"orders:broker:pos:{order_id}"


ORDERS_BROKER_OPEN_ORDERS: Final[str] = "orders:broker:open_orders"

# PnL
ORDERS_PNL_REALIZED: Final[str] = "orders:pnl:realized"
ORDERS_PNL_UNREALIZED: Final[str] = "orders:pnl:unrealized"
ORDERS_PNL_DAY: Final[str] = "orders:pnl:day"


def orders_pnl_per_index(index: str) -> str:
    _validate_index(index)
    return f"orders:pnl:per_index:{index}"


# Buffered persistence (Background drains)
ORDERS_REPORTS_PENDING: Final[str] = "orders:reports:pending"

# Streams
ORDERS_STREAM_ORDER_EVENTS: Final[str] = "orders:stream:order_events"
ORDERS_STREAM_MANUAL_EXIT: Final[str] = "orders:stream:manual_exit"
ORDERS_STREAM_INTENT: Final[str] = "orders:intent:stream"

# Scheduler / lifecycle stream — Scheduler publishes; engines consume.
SYSTEM_STREAM_SCHEDULER_EVENTS: Final[str] = "system:stream:scheduler_events"


# ===========================================================================
# 6. ui:*
# ===========================================================================

UI_VIEW_DASHBOARD: Final[str] = "ui:views:dashboard"
UI_VIEW_POSITIONS_CLOSED_TODAY: Final[str] = "ui:views:positions_closed_today"
UI_VIEW_PNL: Final[str] = "ui:views:pnl"
UI_VIEW_CAPITAL: Final[str] = "ui:views:capital"
UI_VIEW_HEALTH: Final[str] = "ui:views:health"
UI_VIEW_CONFIGS: Final[str] = "ui:views:configs"

UI_DIRTY: Final[str] = "ui:dirty"
UI_PUB_VIEW: Final[str] = "ui:pub:view"
UI_STREAM_HEALTH_ALERTS: Final[str] = "ui:stream:health_alerts"


def ui_view_strategy(index: str) -> str:
    _validate_index(index)
    return f"ui:views:strategy:{index}"


def ui_view_position(index: str) -> str:
    _validate_index(index)
    return f"ui:views:position:{index}"


def ui_view_delta_pcr(index: str) -> str:
    _validate_index(index)
    return f"ui:views:delta_pcr:{index}"


# ===========================================================================
# Heartbeat field names (Schema.md §1.1 — used as HASH fields under
# SYSTEM_HEALTH_HEARTBEATS, not as standalone keys)
# ===========================================================================
HEARTBEAT_FIELDS: Final[tuple[str, ...]] = (
    "init",
    "data_pipeline",
    "strategy:nifty50",
    "strategy:banknifty",
    "order_exec",
    "background:position_ws",
    "background:pnl",
    "background:delta_pcr:nifty50",
    "background:delta_pcr:banknifty",
    "background:token_refresh",
    "background:capital_poll",
    "background:kill_switch_poll",
    "scheduler",
    "health",
    "api_gateway",
)
