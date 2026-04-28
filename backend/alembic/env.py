"""Alembic environment for Premium-Diff backend.

Sources the DSN from `DATABASE_URL` (loaded from the project `.env`) and runs
migrations synchronously through `psycopg`/`asyncpg` adapters. We use SQLAlchemy
2.x's async engine over `asyncpg` because that's the only Postgres driver pinned
in `requirements.txt`.

Run from `backend/`:
    alembic upgrade head
    alembic downgrade base
    alembic revision -m "..."
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Make `state` and `state.config_loader` importable so we get the same .env
# resolution rules as the rest of the backend (.env / ../.env / ../../.env).
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from state.config_loader import get_settings  # noqa: E402  (sys.path tweak above)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# We don't autogenerate; migrations are hand-written.
target_metadata = None


# ---------------------------------------------------------------------------
# DSN resolution
# ---------------------------------------------------------------------------
def _x_args() -> dict[str, str]:
    out: dict[str, str] = {}
    for item in context.get_x_argument(as_dictionary=False):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        out[key] = value
    return out


def _resolve_dsn() -> str:
    """Return the SQLAlchemy-compatible asyncpg DSN.

    Precedence:
      1. -x dsn=... CLI override (alembic -x dsn=postgresql+asyncpg://...)
      2. DATABASE_URL env var (already in SQLAlchemy form on EC2)
      3. settings loaded from .env via state.config_loader
    """
    cli = _x_args().get("dsn")
    if cli:
        return _normalize(cli)

    env_dsn = os.getenv("DATABASE_URL")
    if env_dsn:
        return _normalize(env_dsn)

    return _normalize(get_settings().database_url)


def _resolve_schema() -> str | None:
    """Optional target schema for test isolation (`-x schema=...`)."""
    env_schema = os.getenv("ALEMBIC_SCHEMA")
    if env_schema:
        return env_schema

    schema = _x_args().get("schema")
    return schema or None


def _normalize(dsn: str) -> str:
    """Force the SQLAlchemy + asyncpg dialect form expected by `async_engine_from_config`."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+asyncpg://" + dsn[len("postgres://") :]
    return dsn


# ---------------------------------------------------------------------------
# Offline / online runners
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (writes to stdout)."""
    schema = _resolve_schema()
    context.configure(
        url=_resolve_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    schema = _resolve_schema()
    if schema:
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect via asyncpg and run migrations inside one transaction."""
    schema = _resolve_schema()
    connect_args: dict[str, object] = {}
    if schema:
        connect_args["server_settings"] = {"search_path": schema}

    connectable = create_async_engine(
        _resolve_dsn(),
        poolclass=pool.NullPool,
        future=True,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
