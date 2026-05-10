"""
Strategy Engine entry point.

One process per strategy_id (OS-level isolation). The systemd template
unit `pcr-strategy@<sid>.service` passes `--strategy-id=<sid>`; this
process then only spawns vessels matching that strategy_id from the
shared `strategy:registry` SET.

Each process owns:
  - tick_router_task    Redis pub/sub fan-out to its vessels
  - vessel coroutines   one per (strategy_id, instrument_id) pair for this sid
  - heartbeat_task      writes the engine + per-vessel heartbeat every 5 s
  - display_loop_task   formatted live-block to UI + log every 2 s

Health flag and engine heartbeat are namespaced by strategy_id so the
health engine and frontend can distinguish strategies.

Run:
    python -m engines.strategy --strategy-id=bid_ask_imbalance_v1
"""

from __future__ import annotations

import argparse
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="engines.strategy")
    p.add_argument(
        "--strategy-id",
        default=None,
        help="If set, only spawn vessels for this strategy_id. "
        "When unset, spawns all registered vessels (legacy shared-process mode).",
    )
    return p.parse_args(argv)


async def _amain(strategy_id_filter: str | None) -> int:
    # Configure logger with a sid suffix so per-strategy logs are easy to
    # separate in journalctl.
    logger_name = f"strategy:{strategy_id_filter}" if strategy_id_filter else "strategy"
    configure(engine_name=logger_name)
    log = logger.bind(engine=logger_name)
    log.info(f"strategy: starting (sid_filter={strategy_id_filter})")

    redis_client.init_pools()
    redis_sync = redis_client.get_redis_sync()
    redis_async = redis_client.get_redis()

    # Per-strategy engine_up flag: one writer per process, no cross-strategy
    # collisions on a shared key.
    engine_flag_name = f"strategy:{strategy_id_filter}" if strategy_id_filter else "strategy"
    redis_sync.set(K.system_flag_engine_up(engine_flag_name), "true")

    shutdown = asyncio.Event()

    def _on_signal(*_a: Any) -> None:
        log.warning("strategy: signal received; shutting down")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _on_signal)

    specs = discover_vessels(redis_sync, strategy_id_filter=strategy_id_filter)
    if not specs:
        log.error("strategy: no vessels matched filter; exiting")
        redis_sync.set(K.system_flag_engine_up(engine_flag_name), "false")
        return 1

    log.info(f"strategy: spawning {len(specs)} vessels for {engine_flag_name}")

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

    # Heartbeat writes engine_name + per-vessel fields.
    tasks.append(
        asyncio.create_task(
            heartbeat_task(
                redis_async,
                engine_name=engine_flag_name,
                vessel_keys=vessel_keys,
                shutdown=shutdown,
            ),
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
        done, _pending = await asyncio.wait(
            tasks + [asyncio.create_task(shutdown.wait(), name="shutdown_waiter")],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            if t.get_name() == "shutdown_waiter":
                continue
            if t.exception() is not None:
                log.error(f"task {t.get_name()} crashed: {t.exception()!r}")
        shutdown.set()
        await asyncio.wait(tasks, timeout=10.0)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)

        redis_sync.set(K.system_flag_engine_up(engine_flag_name), "false")
        redis_sync.set(K.system_flag_engine_exited(engine_flag_name), "true")
        log.info("strategy: clean shutdown")
    return 0


def _entrypoint() -> int:
    args = _parse_args(sys.argv[1:])
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        return asyncio.run(_amain(strategy_id_filter=args.strategy_id))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error(f"strategy: unhandled exception: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
