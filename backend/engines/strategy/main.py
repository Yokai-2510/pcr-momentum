"""
Strategy Engine entry point.

Single asyncio event loop hosting:
  - tick_router_task    Redis pub/sub fan-out to vessels
  - vessel coroutines   one per (strategy_id, instrument_id) pair
  - heartbeat_task      writes per-vessel heartbeat every 5 s
  - display_loop_task   formatted live-block to UI + log every 2 s

The process owns ALL writes under `strategy:{sid}:{idx}:*` runtime namespace
and emits typed signals to `strategy:stream:signals`.

Run:
    python -m engines.strategy
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

import uvloop
from loguru import logger

from engines.strategy.heartbeat import heartbeat_task
from engines.strategy.ingestion import TickRouter
from engines.strategy.observability.live_display import display_loop
from engines.strategy.registry import discover_vessels
from engines.strategy.runner import vessel_loop
from log_setup import configure
from state import keys as K
from state import redis_client


async def _amain() -> int:
    configure(engine_name="strategy")
    log = logger.bind(engine="strategy")
    log.info("strategy: starting")

    # Wait briefly for system flag — Init may still be running on a cold boot.
    redis_client.init_pools()
    redis_sync = redis_client.get_redis_sync()
    redis_async = redis_client.get_redis()

    # Mark engine_up flag.
    redis_sync.set(K.system_flag_engine_up("strategy"), "true")

    shutdown = asyncio.Event()

    def _on_signal(*_a: Any) -> None:
        log.warning("strategy: signal received; shutting down")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _on_signal)

    # Discover vessels from Redis registry.
    specs = discover_vessels(redis_sync)
    if not specs:
        log.error("strategy: no vessels discovered; exiting")
        redis_sync.set(K.system_flag_engine_up("strategy"), "false")
        return 1

    log.info(f"strategy: spawning {len(specs)} vessels")

    router = TickRouter()
    vessel_keys = [(s.strategy_id, s.instrument_id) for s in specs]

    tasks: list[asyncio.Task] = []

    # Tick router (subscriber).
    tasks.append(
        asyncio.create_task(router.run(redis_async, shutdown=shutdown), name="tick_router")
    )

    # Per-vessel runners.
    for spec in specs:
        tasks.append(
            asyncio.create_task(
                vessel_loop(
                    spec=spec,
                    redis_async=redis_async,
                    redis_sync=redis_sync,
                    router=router,
                    shutdown=shutdown,
                ),
                name=f"vessel_{spec.strategy_id}_{spec.instrument_id}",
            )
        )

    # Heartbeats.
    tasks.append(
        asyncio.create_task(
            heartbeat_task(redis_async, vessel_keys=vessel_keys, shutdown=shutdown),
            name="heartbeat",
        )
    )

    # Live display.
    tasks.append(
        asyncio.create_task(
            display_loop(
                redis_async,
                redis_sync,
                vessel_keys=vessel_keys,
                shutdown=shutdown,
            ),
            name="display_loop",
        )
    )

    # Wait for shutdown OR any task to exit.
    try:
        done, pending = await asyncio.wait(
            tasks + [asyncio.create_task(shutdown.wait(), name="shutdown_waiter")],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            if t.get_name() == "shutdown_waiter":
                continue
            if t.exception() is not None:
                log.error(f"task {t.get_name()} crashed: {t.exception()!r}")
        shutdown.set()
        # Give tasks a moment to drain
        await asyncio.wait(tasks, timeout=10.0)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)

        redis_sync.set(K.system_flag_engine_up("strategy"), "false")
        redis_sync.set(K.system_flag_engine_exited("strategy"), "true")
        log.info("strategy: clean shutdown")
    return 0


def _entrypoint() -> int:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error(f"strategy: unhandled exception: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
