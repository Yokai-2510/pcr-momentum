"""
engines.init.auth_bootstrap — Sequential_Flow §7 step 6 (Bootstrap Upstox auth).

Outcomes:
  ok=True  — `user:auth:access_token` is set + `system:health:auth = "valid"`
  ok=False — `system:health:auth` set to "invalid" / "missing"; caller decides
             whether to skip the rest of the precheck (idle stack) or hard-fail.

Strategy:
  1. If creds row in user_credentials missing → "missing"; bail.
  2. If we have a recent token in `user:auth:access_token` AND the broker
     `validate_token` probe is True → keep it; "valid".
  3. Else attempt the v3 user-approved token-request flow OR the legacy
     Playwright login. Webhook-driven token arrival is the production path;
     Playwright is a fallback the user can run manually.

This module never raises on broker failures — it returns AuthResult and lets
main() set the appropriate trading_disabled_reason flag.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from state import keys as K


@dataclass(slots=True)
class AuthResult:
    ok: bool
    reason: str  # "valid" | "invalid" | "missing"
    token: str | None
    refreshed: bool


async def _read_creds(redis: _redis_async.Redis) -> dict[str, Any] | None:
    raw = await redis.get(K.USER_CREDENTIALS_UPSTOX)
    if not raw:
        return None
    blob = raw.encode() if isinstance(raw, str) else raw
    parsed: dict[str, Any] = orjson.loads(blob)
    return parsed


async def _read_cached_token(redis: _redis_async.Redis) -> str | None:
    raw = await redis.get(K.USER_AUTH_ACCESS_TOKEN)
    if not raw:
        return None
    text: str = raw.decode() if isinstance(raw, bytes) else raw
    try:
        payload = orjson.loads(text)
    except Exception:
        # Legacy: the value was a bare token string.
        return text
    if isinstance(payload, dict):
        token = payload.get("token")
        return str(token) if token else None
    return None


async def persist_token(redis: _redis_async.Redis, token: str, source: str) -> None:
    payload = {
        "token": token,
        "issued_at": int(time.time() * 1000),
        "expires_at": None,  # Upstox tokens rotate at 03:30 IST; absolute expiry varies
        "source": source,
    }
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.USER_AUTH_ACCESS_TOKEN, orjson.dumps(payload))
    pipe.set(K.USER_AUTH_LAST_REFRESH_TS, str(payload["issued_at"]))
    pipe.set(K.SYSTEM_HEALTH_AUTH, "valid")
    await pipe.execute()


async def ensure_valid_token(redis: _redis_async.Redis) -> AuthResult:
    """Best-effort: keep an existing valid token; otherwise run the v3 request flow.

    Returns AuthResult(ok, reason, token, refreshed).
    """
    creds = await _read_creds(redis)
    if not creds:
        await redis.set(K.SYSTEM_HEALTH_AUTH, "missing")
        return AuthResult(ok=False, reason="missing", token=None, refreshed=False)

    cached = await _read_cached_token(redis)
    if cached and UpstoxAPI.validate_token({"access_token": cached}):
        await redis.set(K.SYSTEM_HEALTH_AUTH, "valid")
        return AuthResult(ok=True, reason="valid", token=cached, refreshed=False)

    # Try v3 request — pushes a notification to the registered notifier webhook.
    res = UpstoxAPI.request_access_token(
        {"creds": {"api_key": creds.get("api_key"), "api_secret": creds.get("api_secret")}}
    )
    if res["success"]:
        logger.info(
            f"auth_bootstrap: v3 token request issued; "
            f"notifier_url={res['data'].get('notifier_url')!r} "
            f"expiry={res['data'].get('authorization_expiry')!r}"
        )
        # The actual token arrives async at the notifier webhook, which is
        # FastAPI's responsibility to consume (Phase 9). Init does NOT block on
        # webhook delivery — we set trading_disabled_reason and let the stack
        # come up idle.
        await redis.set(K.SYSTEM_HEALTH_AUTH, "invalid")
        return AuthResult(ok=False, reason="invalid", token=None, refreshed=False)

    logger.warning(f"auth_bootstrap: token request failed: {res['error']!r}")
    await redis.set(K.SYSTEM_HEALTH_AUTH, "invalid")
    return AuthResult(ok=False, reason="invalid", token=None, refreshed=False)
