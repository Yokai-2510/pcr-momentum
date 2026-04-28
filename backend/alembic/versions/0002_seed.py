"""Seed defaults: admin user + config_settings + config_task_definitions.

Revision ID: 0002_seed
Revises: 0001_initial
Create Date: 2026-04-28

This migration is idempotent — each insert uses `ON CONFLICT DO NOTHING`,
so re-running upgrade head against a populated DB is a safe no-op.

Required env vars (read from `.env` via `state.config_loader.get_settings()`):
    SEED_ADMIN_PASSWORD   plaintext password for the admin user
    JWT_SECRET            used as the user's per-row jwt_secret
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import bcrypt
from alembic import op

# Ensure `state` is importable (env.py does this for live runs; we duplicate
# for offline mode and isolated test runs).
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from state.config_loader import get_settings  # noqa: E402

revision: str = "0002_seed"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Default config payloads — match Pydantic models in `state/schemas/config.py`.
# Values from Schema.md §4 + Strategy.md §14.
# ---------------------------------------------------------------------------
EXECUTION_DEFAULT = {
    "buffer_inr": 2,
    "eod_buffer_inr": 5,
    "spread_skip_pct": 0.05,
    "drift_threshold_inr": 3,
    "chase_ceiling_inr": 15,
    "open_timeout_sec": 8,
    "partial_grace_sec": 3,
    "max_retries": 2,
    "worker_pool_size": 8,
    "liquidity_exit_suppress_after": "15:00",
}

SESSION_DEFAULT = {
    "market_open": "09:15",
    "pre_open_snapshot": "09:14:50",
    "ws_subscribe_at": "09:14:00",
    "delta_pcr_first_compute": "09:18",
    "delta_pcr_interval_minutes": 3,
    "entry_freeze": "15:10",
    "eod_squareoff": "15:15",
    "market_close": "15:30",
    "graceful_shutdown": "15:45",
    "instrument_refresh": "05:30",
}

RISK_DEFAULT = {
    "daily_loss_circuit_pct": 0.08,
    "max_concurrent_positions": 2,
    "trading_capital_inr": 200000,
}


def _index_config(
    *,
    index: str,
    strike_step: int,
    lot_size: int,
    reversal_threshold_inr: int,
) -> dict[str, object]:
    """Strategy.md §14 default IndexConfig (already in IndexConfig pydantic shape)."""
    return {
        "index": index,
        "strike_step": strike_step,
        "lot_size": lot_size,
        "exchange": "NFO",
        "pre_open_subscribe_window": 6,
        "trading_basket_range": 2,
        "reversal_threshold_inr": reversal_threshold_inr,
        "entry_dominance_threshold_inr": reversal_threshold_inr,
        "post_sl_cooldown_sec": 60,
        "post_reversal_cooldown_sec": 90,
        "max_entries_per_day": 8,
        "max_reversals_per_day": 4,
        "qty_lots": 1,
        "sl_pct": 0.20,
        "target_pct": 0.50,
        "tsl_arm_pct": 0.15,
        "tsl_trail_pct": 0.05,
        "max_hold_sec": 25 * 60,
        "delta_pcr_required_for_entry": False,
    }


CONFIG_ROWS: list[tuple[str, dict[str, object]]] = [
    ("execution", EXECUTION_DEFAULT),
    ("session", SESSION_DEFAULT),
    ("risk", RISK_DEFAULT),
    (
        "index:nifty50",
        _index_config(
            index="nifty50", strike_step=50, lot_size=75, reversal_threshold_inr=20
        ),
    ),
    (
        "index:banknifty",
        _index_config(
            index="banknifty", strike_step=100, lot_size=35, reversal_threshold_inr=40
        ),
    ),
]


# ---------------------------------------------------------------------------
# Default scheduler tasks (mirror Schema.md §1.1 control events + Phase 0 plan).
# Times are IST; scheduler runs in IST.
# ---------------------------------------------------------------------------
TASK_ROWS: list[dict[str, object]] = [
    {
        "id": "instrument_refresh",
        "engine": "scheduler",
        "name": "Daily instruments master refresh",
        "cron": None,
        "start_time": "05:30",
        "end_time": None,
        "duration_s": None,
        "event_name": "instrument_refresh",
        "target_engines": ["init", "data_pipeline"],
    },
    {
        "id": "ws_subscribe",
        "engine": "scheduler",
        "name": "Open WebSocket subscriptions for the day",
        "cron": None,
        "start_time": "09:14:00",
        "end_time": None,
        "duration_s": None,
        "event_name": "ws_subscribe",
        "target_engines": ["data_pipeline", "background"],
    },
    {
        "id": "pre_open_snapshot",
        "engine": "scheduler",
        "name": "Snapshot OI baseline before open",
        "cron": None,
        "start_time": "09:14:50",
        "end_time": None,
        "duration_s": None,
        "event_name": "pre_open_snapshot",
        "target_engines": ["background"],
    },
    {
        "id": "session_open",
        "engine": "scheduler",
        "name": "Allow strategy entries",
        "cron": None,
        "start_time": "09:15",
        "end_time": None,
        "duration_s": None,
        "event_name": "session_open",
        "target_engines": ["strategy", "order_exec"],
    },
    {
        "id": "delta_pcr_first_compute",
        "engine": "scheduler",
        "name": "First ΔPCR compute of the day",
        "cron": None,
        "start_time": "09:18",
        "end_time": None,
        "duration_s": None,
        "event_name": "delta_pcr_first_compute",
        "target_engines": ["background"],
    },
    {
        "id": "entry_freeze",
        "engine": "scheduler",
        "name": "Block new entries; exit-only mode",
        "cron": None,
        "start_time": "15:10",
        "end_time": None,
        "duration_s": None,
        "event_name": "entry_freeze",
        "target_engines": ["strategy", "order_exec"],
    },
    {
        "id": "eod_squareoff",
        "engine": "scheduler",
        "name": "Force-close any open positions",
        "cron": None,
        "start_time": "15:15",
        "end_time": None,
        "duration_s": None,
        "event_name": "eod_squareoff",
        "target_engines": ["order_exec"],
    },
    {
        "id": "session_close",
        "engine": "scheduler",
        "name": "Mark session ended",
        "cron": None,
        "start_time": "15:30",
        "end_time": None,
        "duration_s": None,
        "event_name": "session_close",
        "target_engines": ["strategy", "order_exec", "data_pipeline", "background"],
    },
    {
        "id": "graceful_shutdown",
        "engine": "scheduler",
        "name": "Drain queues and stop engines",
        "cron": None,
        "start_time": "15:45",
        "end_time": None,
        "duration_s": None,
        "event_name": "graceful_shutdown",
        "target_engines": [
            "init",
            "data_pipeline",
            "strategy",
            "order_exec",
            "background",
            "scheduler",
            "health",
            "api_gateway",
        ],
    },
    {
        "id": "token_refresh_check",
        "engine": "scheduler",
        "name": "Verify Upstox access token freshness",
        "cron": "*/15 * * * *",
        "start_time": None,
        "end_time": None,
        "duration_s": None,
        "event_name": "token_refresh_check",
        "target_engines": ["background"],
    },
]


# ---------------------------------------------------------------------------
def _admin_password_and_secret() -> tuple[str, str]:
    """Resolve admin seed password + jwt_secret from env / settings.

    Falls back to direct os.environ when alembic is invoked with
    ``-x dsn=...`` against an isolated test database without a `.env`.
    """
    pw = os.getenv("SEED_ADMIN_PASSWORD")
    js = os.getenv("JWT_SECRET")
    if pw and js:
        return pw, js
    s = get_settings()
    pw = pw or s.seed_admin_password
    js = js or s.jwt_secret
    if not pw or not js:
        raise RuntimeError(
            "0002_seed requires SEED_ADMIN_PASSWORD and JWT_SECRET in the environment"
        )
    return pw, js


# ---------------------------------------------------------------------------
def upgrade() -> None:
    pw, jwt_secret = _admin_password_and_secret()
    pw_hash = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    op.execute(
        f"""
        INSERT INTO user_accounts (username, password_hash, jwt_secret, role)
        VALUES ('admin', $${pw_hash}$$, $${jwt_secret}$$, 'admin')
        ON CONFLICT (username) DO NOTHING;
        """
    )

    for key, value in CONFIG_ROWS:
        payload = json.dumps(value).replace("$$", "$ $")
        op.execute(
            f"""
            INSERT INTO config_settings (key, value)
            VALUES ('{key}', $${payload}$$::jsonb)
            ON CONFLICT (key) DO NOTHING;
            """
        )

    for task in TASK_ROWS:
        engines_array = "ARRAY[" + ",".join(f"'{e}'" for e in task["target_engines"]) + "]"
        cron = "NULL" if task["cron"] is None else f"'{task['cron']}'"
        start_time = "NULL" if task["start_time"] is None else f"'{task['start_time']}'"
        end_time = "NULL" if task["end_time"] is None else f"'{task['end_time']}'"
        duration = "NULL" if task["duration_s"] is None else str(task["duration_s"])
        op.execute(
            f"""
            INSERT INTO config_task_definitions
                (id, engine, name, cron, start_time, end_time, duration_s,
                 event_name, target_engines, enabled)
            VALUES (
                '{task["id"]}', '{task["engine"]}', $${task["name"]}$$,
                {cron}, {start_time}, {end_time}, {duration},
                '{task["event_name"]}', {engines_array}::TEXT[], TRUE
            )
            ON CONFLICT (id) DO NOTHING;
            """
        )


def downgrade() -> None:
    op.execute("DELETE FROM config_task_definitions WHERE id IN ("
               + ",".join(f"'{t['id']}'" for t in TASK_ROWS) + ");")
    op.execute("DELETE FROM config_settings WHERE key IN ("
               + ",".join(f"'{k}'" for k, _ in CONFIG_ROWS) + ");")
    op.execute("DELETE FROM user_accounts WHERE username = 'admin';")
