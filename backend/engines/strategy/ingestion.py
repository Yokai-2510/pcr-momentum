"""
Tick ingestion — Redis pub/sub fan-out to vessels.

The data-pipeline PUBLISHes one byte to `market_data:pub:tick:{token}` after
every WS frame is persisted. This module subscribes (collectively, on behalf
of all vessels) and sets each vessel's `dirty` event when one of its tokens
fires.

Why one shared subscriber: a Redis subscription is cheap but not free, and
many vessels will share tokens (NIFTY CE 24350 may matter to two strategies
running on NIFTY). One subscriber, dispatched in-process, is cleanest.

Architecture:

    Redis (data-pipeline writes & PUBLISHes)
                    │
                    ▼
    pub/sub task `tick_subscriber_task`  ───►  per-token routing table
                                                 token -> [vessel_dirty_events]
                                                 .set() on every notification
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import redis.asyncio as _redis_async
from loguru import logger

from state import keys as K


class TickRouter:
    """Routes pub/sub notifications to per-vessel dirty events.

    Vessels register their token interests at startup (and on basket-shift).
    The router maintains a single async subscription that wakes every
    relevant vessel on every notification.
    """

    def __init__(self) -> None:
        # token -> list of asyncio.Event (vessels listening for this token)
        self._routes: dict[str, list[asyncio.Event]] = defaultdict(list)
        # The set of channels we're currently subscribed to.
        self._subscribed: set[str] = set()
        self._pubsub: _redis_async.client.PubSub | None = None
        self._reload_lock = asyncio.Lock()

    def register(self, token: str, dirty: asyncio.Event) -> None:
        """Add a vessel's dirty-event for `token`. Idempotent."""
        if dirty not in self._routes[token]:
            self._routes[token].append(dirty)

    def unregister(self, token: str, dirty: asyncio.Event) -> None:
        if dirty in self._routes[token]:
            self._routes[token].remove(dirty)
        if not self._routes[token]:
            del self._routes[token]

    def desired_channels(self) -> set[str]:
        return {K.market_data_pub_tick(t) for t in self._routes.keys()}

    async def _reconcile_subscriptions(self) -> None:
        """Sync our subscription set to the desired set (additive only here;
        unsubscribe is left for vessel teardown)."""
        if self._pubsub is None:
            return
        async with self._reload_lock:
            desired = self.desired_channels()
            to_add = desired - self._subscribed
            to_drop = self._subscribed - desired
            for ch in to_add:
                await self._pubsub.subscribe(ch)
                self._subscribed.add(ch)
            for ch in to_drop:
                try:
                    await self._pubsub.unsubscribe(ch)
                except Exception:
                    pass
                self._subscribed.discard(ch)

    async def reconcile(self) -> None:
        """Public entry — call after vessel basket changes."""
        await self._reconcile_subscriptions()

    async def run(
        self,
        redis_async: _redis_async.Redis,
        *,
        shutdown: asyncio.Event,
    ) -> None:
        """Main subscriber loop. Blocks until shutdown."""
        log = logger.bind(engine="strategy", component="tick_router")
        self._pubsub = redis_async.pubsub(ignore_subscribe_messages=True)
        await self._reconcile_subscriptions()
        log.info(f"tick_router: started, subscribed_channels={len(self._subscribed)}")

        try:
            while not shutdown.is_set():
                # Skip get_message when nothing is subscribed yet — calling
                # it on an unsubscribed PubSub raises a noisy RuntimeError.
                if not self._subscribed:
                    await asyncio.sleep(0.5)
                    await self._reconcile_subscriptions()
                    continue
                try:
                    msg = await asyncio.wait_for(
                        self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=1.5,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    await self._reconcile_subscriptions()
                    continue
                except Exception as exc:
                    log.warning(f"pubsub get_message error: {exc!r}")
                    await asyncio.sleep(0.5)
                    continue
                if not msg:
                    continue
                channel = msg.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                if not channel or not channel.startswith("market_data:pub:tick:"):
                    continue
                token = channel[len("market_data:pub:tick:"):]
                events = self._routes.get(token, ())
                for ev in events:
                    ev.set()
        finally:
            try:
                if self._pubsub is not None:
                    await self._pubsub.aclose()
            except Exception:
                pass
            log.info("tick_router: shutdown")
