"""Per-strategy daily reports — EOD aggregates + cumulative analysis base.

One row per (report_date, strategy_id): the background engine upserts it on
the scheduler's market_close event by aggregating trades_closed_positions.
Cumulative ("all till today") views are a SUM over this table.

Revision ID: 0004_strategy_daily_reports
Revises: 0003_per_strategy_config_sections
"""

from __future__ import annotations

from alembic import op

revision = "0004_daily_reports"
down_revision = "0003_per_strategy_cfgs"
branch_labels = None
depends_on = None

TABLE = """
CREATE TABLE IF NOT EXISTS strategy_daily_reports (
    id              BIGSERIAL PRIMARY KEY,
    report_date     DATE NOT NULL,
    strategy_id     TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'paper',
    trades          INT NOT NULL DEFAULT 0,
    wins            INT NOT NULL DEFAULT 0,
    losses          INT NOT NULL DEFAULT 0,
    win_rate        NUMERIC NOT NULL DEFAULT 0,
    gross_pnl       NUMERIC NOT NULL DEFAULT 0,
    avg_pnl         NUMERIC NOT NULL DEFAULT 0,
    avg_pnl_pct     NUMERIC NOT NULL DEFAULT 0,
    best_trade_pnl  NUMERIC NOT NULL DEFAULT 0,
    worst_trade_pnl NUMERIC NOT NULL DEFAULT 0,
    avg_hold_sec    INT NOT NULL DEFAULT 0,
    per_instrument  JSONB NOT NULL DEFAULT '{}'::jsonb,
    exit_reasons    JSONB NOT NULL DEFAULT '{}'::jsonb,
    rejected_signals INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (report_date, strategy_id, mode)
);
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_strategy_daily_reports_sid "
        "ON strategy_daily_reports(strategy_id, report_date DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_daily_reports;")
