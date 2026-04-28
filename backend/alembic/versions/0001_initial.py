"""Initial schema — all tables from `docs/Schema.md` §2.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-28
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# DDL — copied verbatim from Schema.md §2 with minor formatting.
# Postgres 16 has `gen_random_uuid()` built-in, no extension required.
# ---------------------------------------------------------------------------
USER_ACCOUNTS = """
CREATE TABLE user_accounts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    jwt_secret    TEXT NOT NULL,
    role          TEXT DEFAULT 'admin',
    created_at    TIMESTAMPTZ DEFAULT now()
);
"""

USER_CREDENTIALS = """
CREATE TABLE user_credentials (
    broker          TEXT PRIMARY KEY,
    encrypted_value BYTEA NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT now()
);
"""

USER_AUDIT_LOG = """
CREATE TABLE user_audit_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ DEFAULT now(),
    user_id    UUID REFERENCES user_accounts(id),
    action     TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT,
    payload    JSONB
);
"""

CONFIG_SETTINGS = """
CREATE TABLE config_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by UUID REFERENCES user_accounts(id)
);
"""

CONFIG_TASK_DEFINITIONS = """
CREATE TABLE config_task_definitions (
    id             TEXT PRIMARY KEY,
    engine         TEXT NOT NULL,
    name           TEXT NOT NULL,
    cron           TEXT,
    start_time     TIME,
    end_time       TIME,
    duration_s     INT,
    event_name     TEXT NOT NULL,
    target_engines TEXT[] NOT NULL,
    enabled        BOOLEAN DEFAULT TRUE
);
"""

MARKET_CALENDAR = """
CREATE TABLE market_calendar (
    date       DATE PRIMARY KEY,
    is_trading BOOLEAN NOT NULL,
    session    JSONB,
    notes      TEXT
);
"""

MARKET_INSTRUMENTS_CACHE = """
CREATE TABLE market_instruments_cache (
    ts             TIMESTAMPTZ NOT NULL,
    index          TEXT NOT NULL,
    expiry         DATE NOT NULL,
    instrument_key TEXT NOT NULL,
    strike         INT,
    type           TEXT,
    lot_size       INT,
    tick_size      NUMERIC,
    PRIMARY KEY (ts, instrument_key)
);
"""

TRADES_CLOSED_POSITIONS = """
CREATE TABLE trades_closed_positions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sig_id                TEXT NOT NULL,
    index                 TEXT NOT NULL,
    mode                  TEXT NOT NULL,
    side                  TEXT NOT NULL,
    strike                INT NOT NULL,
    instrument_token      TEXT NOT NULL,
    qty                   INT NOT NULL,
    entry_ts              TIMESTAMPTZ NOT NULL,
    exit_ts               TIMESTAMPTZ NOT NULL,
    holding_seconds       INT NOT NULL,
    entry_price           NUMERIC NOT NULL,
    exit_price            NUMERIC NOT NULL,
    pnl                   NUMERIC NOT NULL,
    pnl_pct               NUMERIC NOT NULL,
    exit_reason           TEXT NOT NULL,
    intent                TEXT NOT NULL,
    signal_snapshot       JSONB NOT NULL,
    pre_open_snapshot     JSONB NOT NULL,
    market_snapshot_entry JSONB NOT NULL,
    market_snapshot_exit  JSONB NOT NULL,
    exit_eval_history     JSONB,
    trailing_history      JSONB,
    order_events          JSONB NOT NULL,
    latencies             JSONB NOT NULL,
    pnl_breakdown         JSONB NOT NULL,
    delta_pcr_at_entry    NUMERIC,
    delta_pcr_at_exit     NUMERIC,
    raw_broker_responses  JSONB,
    strategy_version      TEXT NOT NULL,
    created_at            TIMESTAMPTZ DEFAULT now()
);
"""

TRADES_REJECTED_SIGNALS = """
CREATE TABLE trades_rejected_signals (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ DEFAULT now(),
    sig_id         TEXT NOT NULL,
    index          TEXT NOT NULL,
    reason         TEXT NOT NULL,
    signal_payload JSONB
);
"""

METRICS_ORDER_EVENTS = """
CREATE TABLE metrics_order_events (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL,
    position_id         UUID,
    order_id            TEXT NOT NULL,
    index               TEXT,
    event_type          TEXT NOT NULL,
    broker_status       TEXT,
    payload             JSONB,
    internal_latency_ms INT
);
"""

METRICS_PNL_HISTORY = """
CREATE TABLE metrics_pnl_history (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL,
    mode       TEXT NOT NULL,
    index      TEXT,
    realized   NUMERIC,
    unrealized NUMERIC,
    open_count INT,
    day_trades INT,
    win_rate   NUMERIC
);
"""

METRICS_DELTA_PCR_HISTORY = """
CREATE TABLE metrics_delta_pcr_history (
    id                   BIGSERIAL PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    index                TEXT NOT NULL,
    spot                 NUMERIC,
    atm                  INT,
    total_d_put_oi       BIGINT,
    total_d_call_oi      BIGINT,
    cumulative_d_put_oi  BIGINT,
    cumulative_d_call_oi BIGINT,
    interval_pcr         NUMERIC,
    cumulative_pcr       NUMERIC,
    per_strike_breakdown JSONB
);
"""

METRICS_HEALTH_HISTORY = """
CREATE TABLE metrics_health_history (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL,
    summary      TEXT NOT NULL,
    engines      JSONB,
    dependencies JSONB
);
"""

METRICS_SYSTEM_EVENTS = """
CREATE TABLE metrics_system_events (
    id      BIGSERIAL PRIMARY KEY,
    ts      TIMESTAMPTZ DEFAULT now(),
    event   TEXT NOT NULL,
    payload JSONB
);
"""

INDEXES = [
    "CREATE INDEX idx_user_audit_log_ts ON user_audit_log(ts DESC);",
    "CREATE INDEX idx_user_audit_log_user_ts ON user_audit_log(user_id, ts DESC);",
    "CREATE INDEX idx_market_instruments_index_expiry ON market_instruments_cache(index, expiry);",
    "CREATE INDEX idx_trades_closed_entry_ts ON trades_closed_positions(entry_ts DESC);",
    "CREATE INDEX idx_trades_closed_index_entry_ts ON trades_closed_positions(index, entry_ts DESC);",
    "CREATE INDEX idx_trades_closed_mode_entry_ts ON trades_closed_positions(mode, entry_ts DESC);",
    "CREATE INDEX idx_trades_closed_sig_id ON trades_closed_positions(sig_id);",
    "CREATE INDEX idx_trades_rejected_ts ON trades_rejected_signals(ts DESC);",
    "CREATE INDEX idx_trades_rejected_index ON trades_rejected_signals(index, ts DESC);",
    "CREATE INDEX idx_metrics_order_events_ts ON metrics_order_events(ts DESC);",
    "CREATE INDEX idx_metrics_order_events_position ON metrics_order_events(position_id, ts DESC);",
    "CREATE INDEX idx_metrics_order_events_order ON metrics_order_events(order_id);",
    "CREATE INDEX idx_metrics_pnl_history_ts ON metrics_pnl_history(ts DESC);",
    "CREATE INDEX idx_metrics_pnl_history_index_ts ON metrics_pnl_history(index, ts DESC);",
    "CREATE INDEX idx_metrics_delta_pcr_index_ts ON metrics_delta_pcr_history(index, ts DESC);",
    "CREATE INDEX idx_metrics_health_history_ts ON metrics_health_history(ts DESC);",
    "CREATE INDEX idx_metrics_system_events_ts ON metrics_system_events(ts DESC);",
    "CREATE INDEX idx_metrics_system_events_event ON metrics_system_events(event, ts DESC);",
]


# Tables in dependency order (children after parents).
TABLES_IN_ORDER = [
    ("user_accounts", USER_ACCOUNTS),
    ("user_credentials", USER_CREDENTIALS),
    ("user_audit_log", USER_AUDIT_LOG),
    ("config_settings", CONFIG_SETTINGS),
    ("config_task_definitions", CONFIG_TASK_DEFINITIONS),
    ("market_calendar", MARKET_CALENDAR),
    ("market_instruments_cache", MARKET_INSTRUMENTS_CACHE),
    ("trades_closed_positions", TRADES_CLOSED_POSITIONS),
    ("trades_rejected_signals", TRADES_REJECTED_SIGNALS),
    ("metrics_order_events", METRICS_ORDER_EVENTS),
    ("metrics_pnl_history", METRICS_PNL_HISTORY),
    ("metrics_delta_pcr_history", METRICS_DELTA_PCR_HISTORY),
    ("metrics_health_history", METRICS_HEALTH_HISTORY),
    ("metrics_system_events", METRICS_SYSTEM_EVENTS),
]


def upgrade() -> None:
    for _name, ddl in TABLES_IN_ORDER:
        op.execute(ddl)
    for stmt in INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    # Drop in reverse order. CASCADE handles the FK from user_audit_log /
    # config_settings → user_accounts and any future cross-table refs.
    for name, _ddl in reversed(TABLES_IN_ORDER):
        op.execute(f"DROP TABLE IF EXISTS {name} CASCADE;")
