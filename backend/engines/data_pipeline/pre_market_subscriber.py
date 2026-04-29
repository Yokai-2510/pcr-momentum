"""
engines.data_pipeline.pre_market_subscriber — Sequential_Flow §10.

Triggered at startup (post-Init): subscribe to all tokens in
`market_data:subscriptions:desired`, then watch for ≥1 frame on every
subscribed token. When all tokens have first-framed (or 30s elapsed in
degraded mode), SET `system:flags:data_pipeline_subscribed=true`.

This is what unblocks the Strategy pre-open snapshot reader (waits on the
flag) and the Background ΔPCR baseline.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from engines.data_pipeline.state import DataPipelineState
from engines.data_pipeline.subscription_manager import bootstrap_subscriptions
from state import keys as K

# Sequential_Flow §10: degraded fallback after 30s.
FIRST_FRAME_DEADLINE_SEC = 30.0
FIRST_FRAME_POLL_SEC = 0.25


async def subscribe_at_premarket(state: DataPipelineState) -> None:
    """Run the post-init subscribe + first-frame gate.

    Steps:
      1. Wait for ws_connected (or shutdown).
      2. Subscribe to all tokens in subscriptions:desired.
      3. Poll until all subscribed tokens have first-framed OR 30s elapsed.
      4. SET data_pipeline_subscribed=true; emit warning XADD if degraded.
    """
    log = logger.bind(loop="pre_market_subscriber")

    # 1. Wait for WS to be open (with a generous timeout).
    try:
        await asyncio.wait_for(state.ws_connected.wait(), timeout=15.0)
    except TimeoutError:
        log.error("WS did not open within 15s; skipping pre-open subscribe")
        await state.redis.xadd(
            K.SYSTEM_PUB_SYSTEM_EVENT,
            {"event": "warn", "source": "data_pipeline", "msg": "ws_open_timeout"},
        )
        return

    if state.shutdown.is_set():
        return

    # 2. Subscribe.
    subscribed_tokens = await bootstrap_subscriptions(state)
    if not subscribed_tokens:
        log.warning("no tokens to subscribe; setting data_pipeline_subscribed=true anyway")
        await state.redis.set(K.SYSTEM_FLAGS_DATA_PIPELINE_SUBSCRIBED, "true")
        return

    # 3. Wait for first frames.
    deadline = time.monotonic() + FIRST_FRAME_DEADLINE_SEC
    while time.monotonic() < deadline:
        if state.shutdown.is_set():
            return
        missing = subscribed_tokens - state.tokens_with_first_frame
        if not missing:
            log.info(f"all {len(subscribed_tokens)} tokens first-framed")
            break
        await asyncio.sleep(FIRST_FRAME_POLL_SEC)

    # 4. Either way, set the gate flag.
    missing = subscribed_tokens - state.tokens_with_first_frame
    await state.redis.set(K.SYSTEM_FLAGS_DATA_PIPELINE_SUBSCRIBED, "true")
    if missing:
        log.warning(
            f"degraded: {len(missing)}/{len(subscribed_tokens)} tokens silent after "
            f"{FIRST_FRAME_DEADLINE_SEC}s — sample={list(missing)[:5]}"
        )
        await state.redis.xadd(
            K.SYSTEM_PUB_SYSTEM_EVENT,
            {
                "event": "warn",
                "source": "data_pipeline",
                "msg": f"{len(missing)}/{len(subscribed_tokens)} silent",
            },
        )
    else:
        log.info("data_pipeline_subscribed=true (all tokens hot)")
