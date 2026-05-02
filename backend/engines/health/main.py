"""
engines.health.main — Health Engine entry point.

Periodic probe loop:
  every HEALTH_PROBE_INTERVAL_SEC (default 10s) →
    - run all probes
    - HSET system:health:dependencies / engines
    - HSET system:health:summary
    - on red transition, XADD system:health:alerts

Heartbeat: every cycle we also HSET our own heartbeat so we appear in
the engine roster.

Run:
    python -m engines.health
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from typing import Any

import asyncpg
import orjson
import redis.asyncio as _redis_async
from loguru import logger

from engines.health import probes
from log_setup import configure
from state import keys as K
from state import postgres_client, redis_client
from state.config_loader import get_settings

PROBE_INTERVAL_SEC = int(os.environ.get("HEALTH_PROBE_INTERVAL_SEC", "10"))


async def _run_probes(
    redis_async: _redis_async.Redis,
    pool: asyncpg.Pool | None,
) -> dict[str, probes.ProbeResult]:
    redis_res, pg_res, ws_res = await asyncio.gather(
        probes.probe_redis(redis_async),
        probes.probe_postgres(pool),
        probes.probe_broker_ws(redis_async),
    )
    # broker REST is sync-only — push to thread pool.
    rest_res = await asyncio.to_thread(probes.probe_broker_rest)
    sys_load = probes.probe_system_load()
    sys_swap = probes.probe_swap()
    return {
        "redis": redis_res,
        "postgres": pg_res,
        "broker_rest": rest_res,
        "broker_ws": ws_res,
        "system_load": sys_load,
        "swap": sys_swap,
    }


async def _publish_results(
    redis_async: _redis_async.Redis,
    deps: dict[str, probes.ProbeResult],
    engines: dict[str, probes.ProbeResult],
    prev_summary: str | None,
) -> str:
    pipe = redis_async.pipeline(transaction=False)
    for name, res in deps.items():
        pipe.hset(  # type: ignore[misc]
            K.SYSTEM_HEALTH_DEPENDENCIES,
            name,
            orjson.dumps(probes.to_dict(res)).decode(),
        )
    for name, res in engines.items():
        pipe.hset(  # type: ignore[misc]
            K.SYSTEM_HEALTH_ENGINES,
            name,
            orjson.dumps(probes.to_dict(res)).decode(),
        )

    summary_status = probes.aggregate_status(
        list(deps.values()) + list(engines.values())
    )
    pipe.hset(  # type: ignore[misc]
        K.SYSTEM_HEALTH_SUMMARY,
        mapping={
            "status": summary_status,
            "ts_ms": str(int(time.time() * 1000)),
        },
    )
    await pipe.execute()

    if summary_status == "red" and prev_summary != "red":
        try:
            red_parts = [
                f"{k}={v[0]}({v[1]})"
                for k, v in {**deps, **engines}.items()
                if v[0] == "red"
            ]
            await redis_async.xadd(  # type: ignore[misc]
                K.SYSTEM_HEALTH_ALERTS,
                {"kind": "summary_red", "reasons": ";".join(red_parts)[:500]},
                maxlen=1000,
                approximate=True,
            )
        except Exception:
            pass

    return summary_status


async def _amain() -> int:
    configure(engine_name="health")
    log = logger.bind(engine="health")
    settings = get_settings()

    redis_client.init_pools()
    redis_async = redis_client.get_redis()

    pool: asyncpg.Pool | None = None
    try:
        await postgres_client.init_pool(settings.database_url)
        pool = postgres_client.get_pool()
    except Exception as e:
        log.warning(f"health: postgres init failed (will probe red): {e!r}")

    await redis_async.set(K.system_flag_engine_up("health"), "true")  # type: ignore[misc]

    shutdown = asyncio.Event()

    def _handle(_sig: int, _frame: Any) -> None:
        log.warning("health: signal received; shutting down")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle)

    log.info(f"health: started (interval={PROBE_INTERVAL_SEC}s)")
    prev_summary: str | None = None

    while not shutdown.is_set():
        cycle_start = time.time()
        try:
            await redis_async.hset(  # type: ignore[misc]
                K.SYSTEM_HEALTH_HEARTBEATS,
                "health",
                str(int(time.time() * 1000)),
            )
            deps = await _run_probes(redis_async, pool)
            engines = await probes.probe_engines(redis_async)
            prev_summary = await _publish_results(
                redis_async, deps, engines, prev_summary
            )
        except Exception as e:
            log.exception(f"health cycle raised: {e!r}")

        elapsed = time.time() - cycle_start
        sleep_for = max(0.5, PROBE_INTERVAL_SEC - elapsed)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=sleep_for)

    await redis_async.set(K.system_flag_engine_up("health"), "false")  # type: ignore[misc]
    await redis_async.set(K.system_flag_engine_exited("health"), "true")  # type: ignore[misc]
    if pool is not None:
        await postgres_client.close_pool()
    log.info("health: clean shutdown")
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"health: unhandled exception: {e!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
