"""
engines.data_pipeline.state — shared mutable state for the data-pipeline loops.

All four async loops (ws_io, tick_processor, subscription_manager,
pre_market_subscriber) cooperate over a single `DataPipelineState`. This is
the only place where cross-loop mutable state lives; keep it small and
explicit. Per-leaf updates to option_chain are buffered in `chain` (a
per-index dict-of-dicts) and flushed to Redis by `tick_processor` every
flush-interval, so we hold a single-writer for the option_chain key.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as _redis_async


@dataclass(slots=True)
class DataPipelineState:
    """Cross-loop mutable state."""

    # ── Wiring ───────────────────────────────────────────────────────────
    redis: _redis_async.Redis
    access_token: str
    indexes: list[str]  # ["nifty50", "banknifty"]

    # Per-index static metadata (populated at startup from Redis):
    # { index: {strike_step, lot_size, spot_token, expiry, atm_at_open, ...} }
    index_meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    # token → (index, strike, "ce"|"pe") lookup, built from option_chain at boot.
    # Spot tokens map to (index, 0, "spot").
    token_index: dict[str, tuple[str, int, str]] = field(default_factory=dict)

    # ── Per-loop primitives ───────────────────────────────────────────────
    # ws_io_loop pushes raw frames; tick_processor_loop drains.
    tick_queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=10_000)
    )

    # Set by ws_io_loop when streamer.on_open fires; cleared on close.
    ws_connected: asyncio.Event = field(default_factory=asyncio.Event)

    # Set by main() to signal graceful shutdown.
    shutdown: asyncio.Event = field(default_factory=asyncio.Event)

    # Reference to the live broker streamer so subscription_manager can
    # call .subscribe()/.unsubscribe() on it.
    streamer: Any = None

    # ── In-memory option_chain (single-writer per index) ─────────────────
    # { index: { "<strike>": { "ce": {...}, "pe": {...} } } }
    chain: dict[str, dict[str, dict[str, dict[str, Any] | None]]] = field(default_factory=dict)

    # In-memory spot snapshot per index: { index: {ltp, prev_close, ts, ...} }
    spot: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Last frame timestamp per token (epoch ms) — used by health monitoring.
    last_frame_ts: dict[str, int] = field(default_factory=dict)

    # Set of tokens that have emitted at least one frame since boot — used by
    # the data_pipeline_subscribed gate (Sequential_Flow §10).
    tokens_with_first_frame: set[str] = field(default_factory=set)

    # ── Counters / observability ─────────────────────────────────────────
    ticks_processed: int = 0
    ticks_dropped: int = 0
    last_flush_ts: int = 0
