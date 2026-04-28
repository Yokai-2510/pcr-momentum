"""Integration tests for Alembic migrations.

These tests run only when `MIGRATION_TEST_DATABASE_URL` is set. That DSN
should point to a dedicated disposable Postgres database (e.g. CI service DB).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_CMD = [sys.executable, "-m", "alembic"]

EXPECTED_TABLES = {
    "user_accounts",
    "user_credentials",
    "user_audit_log",
    "config_settings",
    "config_task_definitions",
    "market_calendar",
    "market_instruments_cache",
    "trades_closed_positions",
    "trades_rejected_signals",
    "metrics_order_events",
    "metrics_pnl_history",
    "metrics_delta_pcr_history",
    "metrics_health_history",
    "metrics_system_events",
}


def _normalize_for_asyncpg(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://") :]
    return dsn


def _run_alembic_upgrade_head(base_dsn: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = base_dsn
    env["SEED_ADMIN_PASSWORD"] = env.get("SEED_ADMIN_PASSWORD", "phase2_admin_pw")
    env["JWT_SECRET"] = env.get("JWT_SECRET", "x" * 32)
    env["CREDS_ENCRYPTION_KEY"] = env.get("CREDS_ENCRYPTION_KEY", "y" * 44)
    env["APP_ENV"] = env.get("APP_ENV", "test")

    proc = subprocess.run(
        [
            *ALEMBIC_CMD,
            "upgrade",
            "head",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"


@pytest.mark.integration
class TestAlembicMigrations:
    @pytest.fixture(autouse=True)
    def _gate(self, migration_db_url: str | None) -> None:
        if not migration_db_url:
            pytest.skip("MIGRATION_TEST_DATABASE_URL not set; skipping migration tests")

    async def test_upgrade_head_is_idempotent_and_seeds_defaults(
        self, migration_db_url: str
    ) -> None:
        base_dsn = _normalize_for_asyncpg(migration_db_url)

        # First run should create all tables + seed rows.
        _run_alembic_upgrade_head(base_dsn)
        # Second run must be a no-op success.
        _run_alembic_upgrade_head(base_dsn)

        conn = await asyncpg.connect(dsn=base_dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT schemaname, tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                """
            )
            by_schema: dict[str, set[str]] = {}
            for row in rows:
                schema_name = str(row["schemaname"])
                by_schema.setdefault(schema_name, set()).add(str(row["tablename"]))

            target_schema = ""
            present: set[str] = set()
            for schema_name, table_names in by_schema.items():
                if table_names >= EXPECTED_TABLES:
                    target_schema = schema_name
                    present = table_names
                    break

            assert target_schema, "could not find schema containing full migrated table set"
            assert present >= EXPECTED_TABLES

            admin = await conn.fetchrow(
                f"SELECT username, role FROM {target_schema}.user_accounts WHERE username = 'admin'"
            )
            assert admin is not None
            assert admin["role"] == "admin"

            cfg_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {target_schema}.config_settings"
            )
            assert int(cfg_count) >= 5

            task_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {target_schema}.config_task_definitions"
            )
            assert int(task_count) >= 8
        finally:
            await conn.close()

        # Optional snapshot-style check if pg_dump is available.
        if shutil.which("pg_dump"):
            dump = subprocess.run(
                [
                    "pg_dump",
                    "--schema-only",
                    "--schema",
                    target_schema,
                    base_dsn,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert dump.returncode == 0, dump.stderr
            schema_sql = dump.stdout
            assert "CREATE TABLE" in schema_sql
            assert "user_accounts" in schema_sql
            assert "config_settings" in schema_sql
