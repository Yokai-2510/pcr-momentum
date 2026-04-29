"""
engines.data_pipeline.ws_io — owns the broker market WS streamer.

The Upstox SDK (`MarketDataStreamerV3`) is callback-based and runs on its
own thread. We bridge it into asyncio:

  - on_message → push raw frame onto `state.tick_queue` via
    `loop.call_soon_threadsafe(queue.put_nowait, frame)`.
  - on_open / on_close → set/clear `state.ws_connected` event from the
    asyncio thread.

The SDK has built-in auto_reconnect with backoff; our loop just monitors
the connection state and logs reconnects. If the SDK gives up
(`autoReconnectStopped`), we set `shutdown` so main can decide.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from brokers.upstox import market_streamer as _streamer_mod
from engines.data_pipeline.aggregator import update_ws_status
from engines.data_pipeline.state import DataPipelineState


def _make_callbacks(state: DataPipelineState, loop: asyncio.AbstractEventLoop) -> dict[str, Any]:
    """Build SDK callbacks that bridge sync → asyncio safely."""
    reconnect_count = {"n": 0}

    def _on_open(*_args: Any, **_kwargs: Any) -> None:
        logger.info("ws_io: streamer open")
        loop.call_soon_threadsafe(state.ws_connected.set)
        # Schedule the Redis status update on the asyncio loop.
        asyncio.run_coroutine_threadsafe(
            update_ws_status(
                state.redis,
                connected=True,
                last_frame_ts=int(time.time() * 1000),
                reconnect_count=reconnect_count["n"],
            ),
            loop,
        )

    def _on_close(*_args: Any, **_kwargs: Any) -> None:
        logger.warning("ws_io: streamer closed")
        loop.call_soon_threadsafe(state.ws_connected.clear)
        asyncio.run_coroutine_threadsafe(
            update_ws_status(state.redis, connected=False),
            loop,
        )

    def _on_error(err: Any = None, *_args: Any, **_kwargs: Any) -> None:
        logger.error(f"ws_io: streamer error: {err!r}")

    def _on_reconnecting(*_args: Any, **_kwargs: Any) -> None:
        reconnect_count["n"] += 1
        logger.warning(f"ws_io: streamer reconnecting (n={reconnect_count['n']})")

    def _on_auto_reconnect_stop(*_args: Any, **_kwargs: Any) -> None:
        logger.error("ws_io: SDK gave up auto-reconnect; signaling shutdown")
        loop.call_soon_threadsafe(state.shutdown.set)

    def _on_message(message: Any = None, *_args: Any, **_kwargs: Any) -> None:
        # The SDK delivers protobuf-decoded dict here. Some versions wrap it
        # in (streamer_self, message). We accept either by ignoring extras.
        if message is None:
            return
        try:
            loop.call_soon_threadsafe(state.tick_queue.put_nowait, message)
        except asyncio.QueueFull:
            # Drop oldest semantics: pop one then push.
            state.ticks_dropped += 1
            try:
                _ = state.tick_queue.get_nowait()
                state.tick_queue.put_nowait(message)
            except Exception:
                pass

    return {
        "on_open": _on_open,
        "on_close": _on_close,
        "on_error": _on_error,
        "on_message": _on_message,
        "on_reconnecting": _on_reconnecting,
        "on_auto_reconnect_stop": _on_auto_reconnect_stop,
    }


def build_and_connect_streamer(state: DataPipelineState) -> Any:
    """Build the SDK streamer with our callbacks and call .connect()."""
    loop = asyncio.get_running_loop()
    callbacks = _make_callbacks(state, loop)

    # IMPORTANT: We pass an empty initial set; subscribe is called explicitly
    # by `subscription_manager.bootstrap_subscriptions` after the WS opens.
    streamer = _streamer_mod.build_streamer(
        access_token=state.access_token,
        instrument_keys=[],
        mode="full",
        auto_reconnect=True,
        reconnect_interval_sec=5,
        reconnect_max_retries=10,
        **callbacks,
    )
    state.streamer = streamer
    streamer.connect()
    return streamer


async def ws_io_loop(state: DataPipelineState) -> None:
    """Spawn the SDK streamer thread, then await shutdown.

    The SDK runs its own thread for I/O; this coroutine just keeps the
    asyncio context alive and lets the streamer's callbacks fire on us.
    Periodically refreshes ws_status so the dashboard shows liveness.
    """
    log = logger.bind(loop="ws_io")
    try:
        build_and_connect_streamer(state)
    except Exception as e:
        log.error(f"failed to start streamer: {e!r}")
        state.shutdown.set()
        return

    last_status_flush = 0
    while not state.shutdown.is_set():
        try:
            await asyncio.sleep(2.0)
            # Refresh ws_status with last_frame_ts (helps dead-tick detection).
            now = int(time.time() * 1000)
            if now - last_status_flush > 5_000:
                last_frame_ts = max(state.last_frame_ts.values()) if state.last_frame_ts else 0
                await update_ws_status(
                    state.redis,
                    connected=state.ws_connected.is_set(),
                    last_frame_ts=last_frame_ts or now,
                )
                last_status_flush = now
        except Exception as e:
            log.error(f"ws_io tick failed: {e!r}")

    # Graceful disconnect
    log.info("ws_io: shutdown signaled; disconnecting streamer")
    try:
        streamer = state.streamer
        if streamer and hasattr(streamer, "disconnect"):
            streamer.disconnect()
    except Exception as e:
        log.warning(f"ws_io: disconnect raised: {e!r}")
    await update_ws_status(state.redis, connected=False)
