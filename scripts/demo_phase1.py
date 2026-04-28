"""Phase 1 demo: verify state primitives can connect, write, and read.

Exit-criteria check from `docs/Project_Plan.md` (Phase 1):
- Redis connect + write + read
- Postgres connect + query

Run from repo root on EC2:

    /home/ubuntu/premium_diff_bot/.venv/bin/python scripts/demo_phase1.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure `backend/` is importable when running from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from state.config_loader import get_settings
from state.postgres_client import close_pool, init_pool
from state.redis_client import close_pools, get_redis, init_pools

DEMO_KEY = "system:lifecycle:phase1_demo_last_ok"


async def main() -> int:
    settings = get_settings()

    # Redis round-trip
    _ = settings.redis_url  # ensures required env is loaded/validated
    init_pools()
    r = get_redis()
    now = datetime.now(timezone.utc).isoformat()
    await r.set(DEMO_KEY, now)
    redis_value = await r.get(DEMO_KEY)

    # Postgres round-trip
    pool = await init_pool(settings.database_url)
    async with pool.acquire() as conn:
        pg_value = await conn.fetchval("SELECT 1")

    ok = redis_value == now and pg_value == 1
    payload = {
        "redis_key": DEMO_KEY,
        "redis_write": now,
        "redis_read": redis_value,
        "postgres_select_1": pg_value,
        "result": "PASS" if ok else "FAIL",
    }
    print(json.dumps(payload, indent=2))

    await close_pool()
    await close_pools()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
