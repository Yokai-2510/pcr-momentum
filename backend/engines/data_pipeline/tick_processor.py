"""
engines.data_pipeline.tick_processor — drain the tick queue, update Redis.

Per TDD §4.2: drains `state.tick_queue`, parses each frame, looks up
(index, strike, side) for each tick token, mutates the in-memory chain /
spot, periodically flushes to Redis (single-writer per option_chain key),
and XADDs a tick event to `market_data:stream:tick:{index}`.

Flush cadence:
  - option_chain JSON: every `FLUSH_INTERVAL_MS` (50ms) per index
  - spot HASH: every tick (cheap; small payload)
  - tick stream XADD: every tick

Backpressure: queue maxsize=10k; on overflow ws_io drops oldest and
increments `state.ticks_dropped`. Health alert is emitted from main().
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import orjson
from loguru import logger

from engines.data_pipeline.aggregator import (
    flush_spot,
    update_option_chain_leaf,
    update_spot_snapshot,
)
from engines.data_pipeline.parser import ParsedTick, parse_tick
from engines.data_pipeline.state import DataPipelineState
from state import keys as K

FLUSH_INTERVAL_MS = 50  # max age of an unsynced option_chain in memory
STREAM_MAXLEN = 10_000  # MAXLEN ~ 10000 (Schema.md §1.3)


async def _emit_tick_stream(redis: Any, index: str, token: str, ltp: float | None, ts: int) -> None:
    """XADD a compact tick event to market_data:stream:tick:{index}."""
    fields = {"token": token, "ltp": str(ltp or 0), "ts": str(ts)}
    await redis.xadd(
        K.market_data_stream_tick(index),
        fields,
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


async def _process_one_tick(state: DataPipelineState, tick: ParsedTick) -> None:
    """Apply a parsed tick to in-memory state. Returns nothing; side-effects only."""
    meta = state.token_index[tick.token]  # caller already filtered unknown tokens
    index, strike, side = meta
    state.last_frame_ts[tick.token] = tick.ts or int(time.time() * 1000)
    state.tokens_with_first_frame.add(tick.token)

    if side == "spot":
        prev_close = float(state.index_meta.get(index, {}).get("prev_close") or 0) or None
        snap = update_spot_snapshot(state.spot.get(index), tick, prev_close)
        state.spot[index] = snap
        # Spot persisted right away (small payload).
        await flush_spot(state.redis, index, snap)
        await _emit_tick_stream(state.redis, index, tick.token, tick.ltp, tick.ts)
        # Notify any vessel subscribed to this spot token (used by ATM-shift logic).
        await state.redis.publish(K.market_data_pub_tick(tick.token), b"")
        return

    # Option leaf: update the in-memory chain. Caller flushes the JSON periodically.
    chain = state.chain.setdefault(index, {})
    update_option_chain_leaf(chain, strike, side, tick)

    # Tick-stream emit (Strategy + Order Exec consume this).
    await state.redis.xadd(
        K.market_data_stream_tick(index),
        {"token": tick.token, "ltp": str(tick.ltp or 0), "ts": str(tick.ts)},
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
    # Pub/sub notification — strategy vessels SUBSCRIBE to tick.{token} for
    # the basket tokens they care about and trigger their decision loop on
    # receipt (Strategy.md §2.3, §9.1). Fire-and-forget; subscribers always
    # read the latest state from Redis on wake-up so no payload is needed.
    await state.redis.publish(K.market_data_pub_tick(tick.token), b"")


async def _flush_chains(state: DataPipelineState, dirty_indexes: set[str]) -> None:
    if not dirty_indexes:
        return
    # Pipeline all dirty index option_chain SETs in one RTT.
    pipe = state.redis.pipeline(transaction=False)
    for idx in dirty_indexes:
        chain = state.chain.get(idx) or {}
        pipe.set(K.market_data_index_option_chain(idx), orjson.dumps(chain))
    await pipe.execute()
    state.last_flush_ts = int(time.time() * 1000)


async def tick_processor_loop(state: DataPipelineState) -> None:
    """Drain ticks, batch-flush option_chains every FLUSH_INTERVAL_MS."""
    log = logger.bind(loop="tick_processor")
    dirty: set[str] = set()
    last_flush = time.monotonic() * 1000

    while not state.shutdown.is_set():
        # Block briefly on the queue.
        try:
            frame: dict[str, Any] = await asyncio.wait_for(state.tick_queue.get(), timeout=0.1)
        except TimeoutError:
            now = time.monotonic() * 1000
            if now - last_flush >= FLUSH_INTERVAL_MS and dirty:
                await _flush_chains(state, dirty)
                dirty.clear()
                last_flush = now
            continue
        except Exception as e:
            log.error(f"queue get failed: {e!r}")
            continue

        ticks = parse_tick(frame)
        for tick in ticks:
            try:
                meta = state.token_index.get(tick.token)
                if meta is None:
                    # Unknown / out-of-window token — drop without counting.
                    continue
                await _process_one_tick(state, tick)
                if meta[2] != "spot":
                    dirty.add(meta[0])
                state.ticks_processed += 1
            except Exception as e:
                log.warning(f"tick apply failed token={tick.token}: {e!r}")

        now = time.monotonic() * 1000
        if now - last_flush >= FLUSH_INTERVAL_MS and dirty:
            await _flush_chains(state, dirty)
            dirty.clear()
            last_flush = now

    # Final flush on shutdown.
    if dirty:
        await _flush_chains(state, dirty)
    log.info(
        f"tick_processor: exiting (processed={state.ticks_processed} dropped={state.ticks_dropped})"
    )
