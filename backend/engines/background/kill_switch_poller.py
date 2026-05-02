"""
engines.background.kill_switch_poller — broker kill-switch snapshot loop.

The Order Exec pre-entry gate reads a *cached* kill-switch snapshot from
`user:capital:kill_switch` (JSON list per segment). This module owns
that cache: it polls `UpstoxAPI.get_kill_switch_status` on a slow
interval (default 60s) and writes the payload back.

Failure handling: on REST error we leave the cache untouched so the gate
keeps using the last-known-good snapshot. Three consecutive failures
emits an alert via `system:health:alerts`.
"""

from __future__ import annotations

import asyncio
import contextlib

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from state import keys as K

_POLL_INTERVAL_SEC = 60
_ALERT_AFTER_CONSEC_FAILURES = 3


async def poll_loop(
    redis_async: _redis_async.Redis,
    *,
    shutdown: asyncio.Event | None = None,
    interval_sec: int = _POLL_INTERVAL_SEC,
) -> None:
    log = logger.bind(engine="background", task="kill_switch_poller")
    log.info(f"kill_switch_poller: started (interval={interval_sec}s)")
    consecutive_failures = 0

    while shutdown is None or not shutdown.is_set():
        token_payload = await redis_async.get(K.USER_AUTH_ACCESS_TOKEN)  # type: ignore[misc]
        if not token_payload:
            await asyncio.sleep(interval_sec)
            continue

        # token blob may be plain string or {"token": "..."} JSON
        token = ""
        try:
            blob = (
                token_payload.decode()
                if isinstance(token_payload, bytes)
                else token_payload
            )
            if blob.startswith("{"):
                parsed = orjson.loads(blob)
                token = parsed.get("token", "") if isinstance(parsed, dict) else ""
            else:
                token = blob
        except Exception:
            token = ""
        if not token:
            await asyncio.sleep(interval_sec)
            continue

        try:
            res = UpstoxAPI.get_kill_switch_status({"access_token": token})
        except Exception as e:
            log.warning(f"kill_switch_poller: REST raised: {e!r}")
            res = {"success": False, "error": str(e)}

        if not res.get("success"):
            consecutive_failures += 1
            if consecutive_failures >= _ALERT_AFTER_CONSEC_FAILURES:
                with contextlib.suppress(Exception):
                    await redis_async.xadd(  # type: ignore[misc]
                        K.SYSTEM_HEALTH_ALERTS,
                        {
                            "kind": "kill_switch_poll_failed",
                            "consec": str(consecutive_failures),
                            "error": str(res.get("error") or ""),
                        },
                        maxlen=1000,
                        approximate=True,
                    )
            await asyncio.sleep(interval_sec)
            continue

        consecutive_failures = 0
        snapshot = res.get("data") or []
        try:
            await redis_async.set(  # type: ignore[misc]
                K.USER_CAPITAL_KILL_SWITCH, orjson.dumps(snapshot).decode()
            )
        except Exception as e:
            log.warning(f"kill_switch_poller: redis SET failed: {e!r}")

        await asyncio.sleep(interval_sec)

    log.info("kill_switch_poller: stopping")
