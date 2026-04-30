"""
engines.order_exec.main — Order Execution Engine entry point.

Topology (TDD §6 + Strategy.md §10):
  - Pre-warmed thread pool of N worker threads (default 8).
  - One async dispatcher task XREADGROUP-tailing strategy:stream:signals.
  - Workers consume from a thread-safe queue.Queue.

Per HLD §4.4: per-index concurrency capped at 1 by the allocator; total
capped at 2. The pool size of 8 is for burst capacity / future extension.

Run:
    python -m engines.order_exec
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import signal
import sys
import threading
import time
from typing import Any

import orjson
from loguru import logger

from engines.order_exec import dispatcher, worker
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


def _read_pool_size(redis_sync: Any) -> int:
    raw = redis_sync.get(K.STRATEGY_CONFIGS_EXECUTION)
    if not raw:
        return 8
    try:
        cfg = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
        return int(cfg.get("worker_pool_size") or 8)
    except Exception:
        return 8


async def _amain() -> int:
    configure(engine_name="order_exec")
    log = logger.bind(engine="order_exec")
    settings = get_settings()

    redis_client.init_pools()
    redis_sync = redis_client.get_redis_sync()
    redis_async = redis_client.get_redis()

    # Postgres pool — required for persisting closed-position reports.
    try:
        await postgres_client.init_pool(settings.database_url)
        pool = postgres_client.get_pool()
    except Exception as e:
        log.error(f"order_exec: postgres init failed: {e!r}")
        return 1

    pool_size = _read_pool_size(redis_sync)
    work_queue: queue.Queue = queue.Queue()

    threads: list[threading.Thread] = []
    for i in range(pool_size):
        t = threading.Thread(
            target=worker.worker_loop,
            args=(work_queue, redis_sync, pool),
            daemon=True,
            name=f"order_exec_worker_{i}",
        )
        t.start()
        threads.append(t)
    log.info(f"order_exec: started {pool_size} worker threads")

    redis_sync.set(K.system_flag_engine_up("order_exec"), "true")
    redis_sync.hset(
        K.SYSTEM_HEALTH_HEARTBEATS,
        "order_exec",
        str(int(time.time() * 1000)),
    )

    shutdown_event = asyncio.Event()

    def _handle(_sig: int, _frame: Any) -> None:
        log.warning("order_exec: signal received; shutting down")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle)

    try:
        await dispatcher.dispatcher_loop(redis_async, work_queue, shutdown=shutdown_event)
    finally:
        # Sentinel-shutdown the workers.
        for _ in threads:
            work_queue.put(None)
        for t in threads:
            t.join(timeout=5.0)
        redis_sync.set(K.system_flag_engine_up("order_exec"), "false")
        redis_sync.set(K.system_flag_engine_exited("order_exec"), "true")
        await postgres_client.close_pool()

    log.info("order_exec: clean shutdown")
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"order_exec: unhandled exception: {e!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
