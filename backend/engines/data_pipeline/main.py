"""
engines.data_pipeline.main — orchestrator for the always-on tick pipeline.

Loop topology (TDD §4.5):
    asyncio.gather(
        ws_io_loop,                       # owns broker WS + reconnect
        tick_processor_loop,              # drains queue → Redis
        subscription_manager_loop,        # ATM-shift watcher
        pre_market_subscriber.subscribe_at_premarket,   # one-shot post-Init
    )

Boot:
  1. Initialize Redis pool, validate auth token presence.
  2. Load per-index meta + option_chain template (set by Init step 11) into
     `DataPipelineState`. Build the token → (index, strike, side) lookup.
  3. SET `system:flags:engine_up:data_pipeline=true`.
  4. Run loops until Ctrl-C / SIGTERM / `state.shutdown` set.

Exit codes:
  0 — clean shutdown
  1 — fatal (no token, Redis down, no chain to subscribe to, etc.)

Run: `python -m engines.data_pipeline`
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from typing import Any

import orjson
from loguru import logger

from engines.data_pipeline import (
    pre_market_subscriber,
    subscription_manager,
    tick_processor,
    ws_io,
)
from engines.data_pipeline.state import DataPipelineState
from log_setup import configure
from state import keys as K
from state import redis_client


async def _read_str(redis: Any, key: str, default: str = "") -> str:
    raw = await redis.get(key)
    if raw is None:
        return default
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def _load_access_token(redis: Any) -> str | None:
    raw = await redis.get(K.USER_AUTH_ACCESS_TOKEN)
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        payload = orjson.loads(text)
        if isinstance(payload, dict):
            tok = payload.get("token")
            return str(tok) if tok else None
    except Exception:
        pass
    return text  # legacy: bare string


async def _load_state(redis: Any) -> DataPipelineState | None:
    """Hydrate DataPipelineState from Redis. Returns None on fatal config error."""
    token = await _load_access_token(redis)
    if not token:
        logger.error("data_pipeline: no access_token in Redis; cannot start WS")
        return None

    indexes: list[str] = list(K.INDEXES)
    state = DataPipelineState(redis=redis, access_token=token, indexes=indexes)

    # Load per-index meta + option_chain template.
    pipe = redis.pipeline(transaction=False)
    for idx in indexes:
        pipe.get(K.market_data_index_meta(idx))
        pipe.get(K.market_data_index_option_chain(idx))
    raw_blobs = await pipe.execute()

    for i, idx in enumerate(indexes):
        meta_raw = raw_blobs[2 * i]
        chain_raw = raw_blobs[2 * i + 1]
        if meta_raw:
            state.index_meta[idx] = orjson.loads(meta_raw)
        if chain_raw:
            state.chain[idx] = orjson.loads(chain_raw)

    # Build token → (index, strike, side) lookup.
    for idx in indexes:
        meta = state.index_meta.get(idx, {})
        spot_token = meta.get("spot_token")
        if spot_token:
            state.token_index[spot_token] = (idx, 0, "spot")

        chain = state.chain.get(idx, {})
        for strike_str, sides in chain.items():
            try:
                strike = int(strike_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(sides, dict):
                continue
            for side in ("ce", "pe"):
                leaf = sides.get(side)
                if leaf and leaf.get("token"):
                    state.token_index[leaf["token"]] = (idx, strike, side)

    if not state.token_index:
        logger.error("data_pipeline: no tokens in option_chain templates; did Init step 11 run?")
        return None

    logger.info(
        f"data_pipeline: state loaded — indexes={indexes} tokens_total={len(state.token_index)}"
    )
    return state


def _install_signal_handlers(state: DataPipelineState) -> None:
    def _handle(signum: int, _frame: Any) -> None:
        logger.warning(f"data_pipeline: signal {signum} received; shutting down")
        state.shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle)


async def main() -> int:
    configure(engine_name="data_pipeline")
    log = logger.bind(engine="data_pipeline")

    # ── Connect Redis ──────────────────────────────────────────────────
    try:
        redis_client.init_pools()
        redis = redis_client.get_redis()
        await redis.ping()  # type: ignore[misc]
    except Exception as e:
        log.error(f"redis connect failed: {e}")
        return 1

    state = await _load_state(redis)
    if state is None:
        return 1

    _install_signal_handlers(state)

    # Mark engine_up before kicking the WS — Health watches this.
    await redis.set(K.system_flag_engine_up("data_pipeline"), "true")
    await redis.hset(  # type: ignore[misc]
        K.SYSTEM_HEALTH_HEARTBEATS,
        mapping={"data_pipeline": int(time.time() * 1000)},
    )

    # ── Run all loops ──────────────────────────────────────────────────
    log.info("data_pipeline: starting loops")
    try:
        await asyncio.gather(
            ws_io.ws_io_loop(state),
            tick_processor.tick_processor_loop(state),
            subscription_manager.subscription_manager_loop(state),
            pre_market_subscriber.subscribe_at_premarket(state),
        )
    except asyncio.CancelledError:
        log.warning("data_pipeline: cancelled")
    except Exception as e:
        log.error(f"data_pipeline: fatal in loops: {e!r}")
        return 1
    finally:
        await redis.set(K.system_flag_engine_up("data_pipeline"), "false")
        await redis.set(K.system_flag_engine_exited("data_pipeline"), "true")

    log.info(
        f"data_pipeline: clean shutdown — processed={state.ticks_processed} "
        f"dropped={state.ticks_dropped}"
    )
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"data_pipeline: unhandled exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
