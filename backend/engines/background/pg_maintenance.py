"""
engines.background.pg_maintenance — nightly VACUUM ANALYZE.

Triggered by the Scheduler's `nightly_maintenance` event (02:00 IST). We
target the high-churn tables explicitly rather than database-wide
`VACUUM ANALYZE` to keep wall-clock time bounded on the small EC2.

The list is conservative — adjust when new high-write tables ship.
"""

from __future__ import annotations

import asyncpg
from loguru import logger

_VACUUM_TABLES = (
    "trades_orders",
    "trades_closed_positions",
    "metrics_runtime",
    "logs_events",
)


async def run_vacuum_analyze(pool: asyncpg.Pool) -> dict[str, str]:
    """VACUUM ANALYZE each target table. Returns per-table status."""
    log = logger.bind(engine="background", task="pg_maintenance")
    results: dict[str, str] = {}
    for table in _VACUUM_TABLES:
        try:
            async with pool.acquire() as conn:
                # asyncpg won't let VACUUM run inside an implicit txn; use raw.
                await conn.execute(f"VACUUM (ANALYZE) {table}")
            results[table] = "ok"
            log.info(f"VACUUM ANALYZE {table} ok")
        except Exception as e:
            results[table] = f"failed:{e!r}"
            log.warning(f"VACUUM ANALYZE {table} failed: {e!r}")
    return results
