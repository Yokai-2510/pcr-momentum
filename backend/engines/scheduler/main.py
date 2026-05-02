"""
engines.scheduler.main — APScheduler entry point.

Reads cron times from `strategy:configs:session` (Schema.md §1.4 / seed in
`alembic/versions/0002_seed.py`). Falls back to plan defaults if the
config key is missing.

We use the AsyncIO scheduler so the job functions can await the async
Redis client directly. No thread-pool gymnastics.

Heartbeat: every 5s we HSET `system:health:heartbeats.scheduler` so Health
can detect a wedged scheduler.

Run:
    python -m engines.scheduler
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from typing import Any

import orjson
import redis.asyncio as _redis_async
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from engines.scheduler import jobs
from log_setup import configure
from state import keys as K
from state import redis_client

_DEFAULT_TRIGGERS: dict[str, str] = {
    "instrument_refresh": "05:30",
    "pre_open_snapshot": "09:14",
    "market_open": "09:15",
    "entry_freeze": "15:10",
    "eod_squareoff": "15:15",
    "market_close": "15:30",
    "daily_reset": "15:35",
    "nightly_maintenance": "02:00",
}

_TIMEZONE = "Asia/Kolkata"


def _parse_hhmm(raw: str) -> tuple[int, int]:
    h, m = raw.split(":", 1)
    return int(h), int(m)


async def _read_session_config(
    redis_async: _redis_async.Redis,
) -> dict[str, str]:
    raw = await redis_async.get(K.STRATEGY_CONFIGS_SESSION)  # type: ignore[misc]
    if not raw:
        return {}
    blob = raw if isinstance(raw, str) else raw.decode()
    try:
        parsed = orjson.loads(blob)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: str(v) for k, v in parsed.items()}


def _resolve_trigger_table(session: dict[str, str]) -> dict[str, str]:
    out = dict(_DEFAULT_TRIGGERS)
    # Map seed-config field names → trigger names.
    mapping = {
        "instrument_refresh": "instrument_refresh",
        "pre_open_snapshot": "pre_open_snapshot",
        "market_open": "market_open",
        "entry_freeze": "entry_freeze",
        "eod_squareoff": "eod_squareoff",
        "market_close": "market_close",
    }
    for cfg_key, trigger_name in mapping.items():
        v = session.get(cfg_key)
        if isinstance(v, str) and ":" in v:
            # Take only HH:MM
            parts = v.split(":")
            if len(parts) >= 2:
                out[trigger_name] = f"{parts[0]}:{parts[1]}"
    return out


def _make_job(
    redis_async: _redis_async.Redis, fn_name: str
) -> Any:
    """Bind a redis-aware coroutine to APScheduler."""
    fn = getattr(jobs, fn_name)

    async def _runner() -> None:
        log = logger.bind(engine="scheduler", trigger=fn_name)
        try:
            await fn(redis_async)
            log.info(f"trigger {fn_name} fired")
        except Exception as e:
            log.exception(f"trigger {fn_name} raised: {e!r}")

    _runner.__name__ = f"job_{fn_name}"
    return _runner


def _add_cron(
    scheduler: AsyncIOScheduler,
    redis_async: _redis_async.Redis,
    job_name: str,
    hhmm: str,
) -> None:
    h, m = _parse_hhmm(hhmm)
    if job_name == "daily_reset":
        # daily_reset has no config field; keep default
        pass
    scheduler.add_job(
        _make_job(redis_async, job_name),
        CronTrigger(hour=h, minute=m, timezone=_TIMEZONE),
        id=job_name,
        replace_existing=True,
    )


async def _heartbeat_loop(
    redis_async: _redis_async.Redis, *, shutdown: asyncio.Event
) -> None:
    while not shutdown.is_set():
        with contextlib.suppress(Exception):
            await redis_async.hset(  # type: ignore[misc]
                K.SYSTEM_HEALTH_HEARTBEATS,
                "scheduler",
                str(int(time.time() * 1000)),
            )
        await asyncio.sleep(5)


async def _amain() -> int:
    configure(engine_name="scheduler")
    log = logger.bind(engine="scheduler")

    redis_client.init_pools()
    redis_async = redis_client.get_redis()

    session_cfg = await _read_session_config(redis_async)
    triggers = _resolve_trigger_table(session_cfg)

    scheduler = AsyncIOScheduler(timezone=_TIMEZONE)
    for name, hhmm in triggers.items():
        _add_cron(scheduler, redis_async, name, hhmm)
    # daily_reset uses the default
    if "daily_reset" not in triggers:
        _add_cron(scheduler, redis_async, "daily_reset", _DEFAULT_TRIGGERS["daily_reset"])
    if "nightly_maintenance" not in triggers:
        _add_cron(
            scheduler, redis_async, "nightly_maintenance",
            _DEFAULT_TRIGGERS["nightly_maintenance"],
        )

    scheduler.start()
    await redis_async.set(K.system_flag_engine_up("scheduler"), "true")  # type: ignore[misc]
    log.info(
        "scheduler: started; jobs="
        + ", ".join(f"{j.id}@{triggers.get(j.id, '?')}" for j in scheduler.get_jobs())
    )

    shutdown = asyncio.Event()

    def _handle(_sig: int, _frame: Any) -> None:
        log.warning("scheduler: signal received; shutting down")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle)

    hb = asyncio.create_task(_heartbeat_loop(redis_async, shutdown=shutdown))
    try:
        await shutdown.wait()
    finally:
        scheduler.shutdown(wait=False)
        hb.cancel()
        with contextlib.suppress(Exception):
            await hb
        await redis_async.set(K.system_flag_engine_up("scheduler"), "false")  # type: ignore[misc]
        await redis_async.set(K.system_flag_engine_exited("scheduler"), "true")  # type: ignore[misc]

    log.info("scheduler: clean shutdown")
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"scheduler: unhandled exception: {e!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
