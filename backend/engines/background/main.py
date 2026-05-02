"""
engines.background.main — Background Engine entry point.

Topology:
  - report_drainer task   (always-on; BLPOP-driven)
  - kill_switch_poller    (always-on; 60s cadence)
  - scheduler_events_consumer (always-on; reacts to Scheduler stream)

Scheduler events handled here:
  - instrument_refresh    → engines.background.instrument_refresh.refresh
  - nightly_maintenance   → engines.background.pg_maintenance.run_vacuum_analyze
                            + engines.background.log_rotation.record_run
  - daily_reset           → clears `orders:positions:closed_today`,
                            `system:flags:daily_loss_circuit_triggered`

All tasks share one event loop, one Redis pool, and one Postgres pool —
isolating Background from the cross-loop pitfalls that broke Phase 7.

Run:
    python -m engines.background
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from typing import Any

import asyncpg
import redis.asyncio as _redis_async
from loguru import logger

from engines.background import (
    instrument_refresh,
    kill_switch_poller,
    log_rotation,
    pg_maintenance,
    report_drainer,
)
from log_setup import configure
from state import keys as K
from state import postgres_client, redis_client
from state.config_loader import get_settings


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


SCHEDULER_GROUP = "background"
SCHEDULER_BLOCK_MS = 1000


async def _ensure_scheduler_group(redis_async: _redis_async.Redis) -> None:
    try:
        await redis_async.xgroup_create(  # type: ignore[misc]
            K.SYSTEM_STREAM_SCHEDULER_EVENTS,
            SCHEDULER_GROUP,
            id="$",
            mkstream=True,
        )
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.bind(engine="background").warning(
                f"xgroup_create scheduler group raised: {e!r}"
            )


async def _on_daily_reset(redis_async: _redis_async.Redis) -> None:
    log = logger.bind(engine="background", task="daily_reset")
    pipe = redis_async.pipeline(transaction=False)
    pipe.delete(K.ORDERS_POSITIONS_CLOSED_TODAY)
    pipe.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    pipe.delete(K.ORDERS_PNL_DAY)
    await pipe.execute()
    log.info("daily_reset: closed_today + dlc + pnl_day cleared")


async def _scheduler_consumer(
    redis_async: _redis_async.Redis,
    pool: asyncpg.Pool,
    *,
    shutdown: asyncio.Event,
    consumer_name: str = "background-1",
) -> None:
    log = logger.bind(engine="background", task="scheduler_consumer")
    await _ensure_scheduler_group(redis_async)
    log.info("scheduler_consumer: started")

    while not shutdown.is_set():
        try:
            resp = await redis_async.xreadgroup(  # type: ignore[misc]
                SCHEDULER_GROUP,
                consumer_name,
                {K.SYSTEM_STREAM_SCHEDULER_EVENTS: ">"},
                count=5,
                block=SCHEDULER_BLOCK_MS,
            )
        except Exception as e:
            log.warning(f"xreadgroup failed: {e!r}")
            await asyncio.sleep(0.5)
            continue
        if not resp:
            continue

        for _stream, entries in resp:
            for entry_id, fields in entries:
                payload = {_decode(k): _decode(v) for k, v in fields.items()}
                kind = payload.get("kind", "")
                log.info(f"scheduler event: {kind}")
                try:
                    if kind == "instrument_refresh":
                        await instrument_refresh.refresh(redis_async)
                    elif kind == "nightly_maintenance":
                        await pg_maintenance.run_vacuum_analyze(pool)
                        await log_rotation.record_run(redis_async)
                    elif kind == "daily_reset":
                        await _on_daily_reset(redis_async)
                    else:
                        log.info(f"scheduler event ignored: {kind}")
                except Exception as e:
                    log.exception(f"handler {kind} raised: {e!r}")
                finally:
                    await redis_async.xack(  # type: ignore[misc]
                        K.SYSTEM_STREAM_SCHEDULER_EVENTS,
                        SCHEDULER_GROUP,
                        entry_id,
                    )


async def _heartbeat_loop(
    redis_async: _redis_async.Redis, *, shutdown: asyncio.Event
) -> None:
    while not shutdown.is_set():
        with contextlib.suppress(Exception):
            await redis_async.hset(  # type: ignore[misc]
                K.SYSTEM_HEALTH_HEARTBEATS,
                "background",
                str(int(time.time() * 1000)),
            )
        await asyncio.sleep(5)


async def _amain() -> int:
    configure(engine_name="background")
    log = logger.bind(engine="background")
    settings = get_settings()

    redis_client.init_pools()
    redis_async = redis_client.get_redis()

    try:
        await postgres_client.init_pool(settings.database_url)
        pool = postgres_client.get_pool()
    except Exception as e:
        log.error(f"postgres init failed: {e!r}")
        return 1

    await redis_async.set(K.system_flag_engine_up("background"), "true")  # type: ignore[misc]

    shutdown = asyncio.Event()

    def _handle(_sig: int, _frame: Any) -> None:
        log.warning("background: signal received; shutting down")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle)

    tasks = [
        asyncio.create_task(
            report_drainer.drain_loop(redis_async, pool, shutdown=shutdown),
            name="report_drainer",
        ),
        asyncio.create_task(
            kill_switch_poller.poll_loop(redis_async, shutdown=shutdown),
            name="kill_switch_poller",
        ),
        asyncio.create_task(
            _scheduler_consumer(redis_async, pool, shutdown=shutdown),
            name="scheduler_consumer",
        ),
        asyncio.create_task(
            _heartbeat_loop(redis_async, shutdown=shutdown),
            name="heartbeat",
        ),
    ]

    log.info(f"background: started {len(tasks)} tasks")
    try:
        await shutdown.wait()
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
        await redis_async.set(K.system_flag_engine_up("background"), "false")  # type: ignore[misc]
        await redis_async.set(K.system_flag_engine_exited("background"), "true")  # type: ignore[misc]
        await postgres_client.close_pool()

    log.info("background: clean shutdown")
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"background: unhandled exception: {e!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
