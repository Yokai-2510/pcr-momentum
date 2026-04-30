"""Strategy Engine entry point.

The process hosts one StrategyInstance thread per enabled index. Each thread
owns writes under its `strategy:{index}:*` runtime namespace and emits typed
signals to `strategy:stream:signals`.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from contextlib import suppress
from typing import Any

import orjson
from loguru import logger

from engines.strategy.strategies import (
    BANKNIFTYStrategy,
    NIFTY50Strategy,
    StrategyInstance,
)
from log_setup import configure
from state import keys as K
from state import redis_client
from state.schemas.config import IndexConfig

_INDEX_TO_CLASS: dict[str, type[StrategyInstance]] = {
    "nifty50": NIFTY50Strategy,
    "banknifty": BANKNIFTYStrategy,
}


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _read_index_config(redis_sync: Any, index: str) -> IndexConfig | None:
    """Read `strategy:configs:indexes:{index}` from Redis."""
    raw = redis_sync.get(K.strategy_config_index(index))
    if not raw:
        return None
    payload = orjson.loads(raw)
    return IndexConfig.model_validate(payload)


def _read_enabled_indexes(redis_sync: Any) -> list[str]:
    out: list[str] = []
    for idx in K.INDEXES:
        flag = _decode(redis_sync.get(K.strategy_enabled(idx))).lower()
        if flag == "true":
            out.append(idx)
    return out


def main() -> int:
    configure(engine_name="strategy")
    log = logger.bind(engine="strategy")

    redis_client.init_pools()
    redis_sync = redis_client.get_redis_sync()

    enabled = _read_enabled_indexes(redis_sync)
    if not enabled:
        log.error("strategy: no indexes enabled at boot")
        return 1

    instances: list[StrategyInstance] = []
    threads: list[threading.Thread] = []

    for idx in enabled:
        cls = _INDEX_TO_CLASS.get(idx)
        if cls is None:
            log.warning(f"strategy: unknown index {idx!r}; skipping")
            continue
        cfg = _read_index_config(redis_sync, idx)
        if cfg is None:
            log.error(f"strategy: missing config for {idx!r}; skipping")
            continue
        instance = cls(redis_sync, cfg)
        thread = threading.Thread(target=instance.run, daemon=True, name=f"strategy_{idx}")
        instances.append(instance)
        threads.append(thread)
        thread.start()
        log.info(f"strategy: thread started for {idx}")

    if not threads:
        log.error("strategy: no threads started")
        return 1

    redis_sync.set(K.system_flag_engine_up("strategy"), "true")
    redis_sync.hset(
        K.SYSTEM_HEALTH_HEARTBEATS,
        "strategy",
        str(int(time.time() * 1000)),
    )

    shutting_down = threading.Event()

    def _handle(_sig: int, _frame: Any) -> None:
        log.warning("strategy: signal received; requesting shutdown")
        shutting_down.set()
        for instance in instances:
            instance.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(Exception):
            signal.signal(sig, _handle)

    while any(thread.is_alive() for thread in threads):
        if shutting_down.is_set():
            break
        redis_sync.hset(
            K.SYSTEM_HEALTH_HEARTBEATS,
            "strategy",
            str(int(time.time() * 1000)),
        )
        time.sleep(1.0)

    for thread in threads:
        thread.join(timeout=10.0)

    redis_sync.set(K.system_flag_engine_up("strategy"), "false")
    redis_sync.set(K.system_flag_engine_exited("strategy"), "true")
    log.info("strategy: clean shutdown")
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error(f"strategy: unhandled exception: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
