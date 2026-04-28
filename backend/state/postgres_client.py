"""Single source of the asyncpg pool shared by every engine that needs Postgres.

Per `docs/Modular_Design.md` §4. Engines that touch durable state (Init,
Order Exec, Background, FastAPI Gateway) share one process-wide pool.

Usage:

    from state.postgres_client import init_pool, get_pool, close_pool, transaction

    await init_pool()
    async with transaction() as conn:
        await conn.execute("INSERT ...")
    pool = get_pool()
    rows = await pool.fetch("SELECT ...")
    await close_pool()
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import asyncpg

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_pool: asyncpg.Pool | None = None

_DEFAULT_MIN_SIZE: Final[int] = 2
_DEFAULT_MAX_SIZE: Final[int] = 10
_DEFAULT_TIMEOUT_SEC: Final[float] = 30.0


def _normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


# ---------------------------------------------------------------------------
# Pool init / teardown
# ---------------------------------------------------------------------------
async def init_pool(
    dsn: str | None = None,
    *,
    min_size: int = _DEFAULT_MIN_SIZE,
    max_size: int = _DEFAULT_MAX_SIZE,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> asyncpg.Pool:
    """Create the process-wide asyncpg pool.

    DSN precedence:
        1. explicit `dsn` argument
        2. `DATABASE_URL` env var
        3. raises `RuntimeError`

    Idempotent: returns the existing pool if already created.
    """
    global _pool
    if _pool is not None:
        return _pool

    resolved = dsn or os.getenv("DATABASE_URL")
    if not resolved:
        raise RuntimeError(
            "Postgres DSN not provided; set DATABASE_URL or pass dsn=..."
        )
    resolved = _normalize_dsn(resolved)

    _pool = await asyncpg.create_pool(
        dsn=resolved,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        command_timeout=timeout,
    )
    return _pool


def get_pool() -> asyncpg.Pool:
    """Return the process-wide pool. Call `init_pool()` first."""
    if _pool is None:
        raise RuntimeError("Postgres pool not initialised; call init_pool() first")
    return _pool


async def close_pool() -> None:
    """Close the pool. Idempotent."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


# ---------------------------------------------------------------------------
# Test-injection helper
# ---------------------------------------------------------------------------
def set_pool_for_testing(pool: asyncpg.Pool | None) -> None:
    """Swap in / clear the pool for tests."""
    global _pool
    _pool = pool


# ---------------------------------------------------------------------------
# Transaction helper
# ---------------------------------------------------------------------------
@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection inside an autocommit-on-success transaction.

    Example:
        async with transaction() as conn:
            await conn.execute("INSERT INTO ...")
            await conn.execute("UPDATE ...")
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn


# ---------------------------------------------------------------------------
# Convenience: simple health check
# ---------------------------------------------------------------------------
async def ping() -> bool:
    """Return True iff a trivial `SELECT 1` succeeds on the pool."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result: int | None = await conn.fetchval("SELECT 1")
    return result == 1
