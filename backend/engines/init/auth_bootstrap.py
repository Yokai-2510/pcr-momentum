"""
engines.init.auth_bootstrap — Sequential_Flow §7 step 6 (Bootstrap Upstox auth).

Strategy:
  1. If creds row missing → "missing"; bail.
  2. If cached token valid (`UpstoxAPI.validate_token`) → reuse it.
  3. Else attempt full Playwright OAuth flow (mobile → TOTP → PIN → auth_code
     → exchange for access_token). Persist + return ok.
  4. If Playwright fails (no browser, creds wrong, page changed):
       fall back to v3 user-approved request flow (kicks notifier webhook;
       returns invalid because the token arrives async — stack stays idle
       until FastAPI's webhook consumer persists it).

This module never raises on broker failures — it returns AuthResult.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from brokers.upstox.auth import exchange_code_for_token, fetch_auth_code
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
        return text
    if isinstance(payload, dict):
        token = payload.get("token")
        return str(token) if token else None
    return None


async def persist_token(redis: _redis_async.Redis, token: str, source: str) -> None:
    payload = {
        "token": token,
        "issued_at": int(time.time() * 1000),
        "expires_at": None,
        "source": source,
    }
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.USER_AUTH_ACCESS_TOKEN, orjson.dumps(payload))
    pipe.set(K.USER_AUTH_LAST_REFRESH_TS, str(payload["issued_at"]))
    pipe.set(K.SYSTEM_HEALTH_AUTH, "valid")
    await pipe.execute()


async def _refresh_via_playwright(
    redis: _redis_async.Redis, creds: dict[str, Any], headless: bool = True
) -> str | None:
    """Drive Playwright login on a worker thread (sync) → exchange code → persist."""

    def _do_sync() -> str:
        # fetch_auth_code lazy-imports playwright + pyotp
        auth_code = fetch_auth_code(
            creds,
            auth_cfg={
                "headless": headless,
                "max_retries": 3,
                "screenshot_dir": "/tmp/upstox_auth",
            },
        )
        return exchange_code_for_token(creds, auth_code)

    try:
        token = await asyncio.to_thread(_do_sync)
    except Exception as e:
        logger.error(f"auth_bootstrap: playwright login failed: {e}")
        return None

    await persist_token(redis, token, source="playwright")
    return token


async def ensure_valid_token(
    redis: _redis_async.Redis,
    *,
    allow_playwright: bool = True,
    headless: bool = True,
) -> AuthResult:
    """Probe → reuse if valid; refresh via Playwright; else v3-webhook-init."""
    creds = await _read_creds(redis)
    if not creds:
        await redis.set(K.SYSTEM_HEALTH_AUTH, "missing")
        return AuthResult(ok=False, reason="missing", token=None, refreshed=False)

    cached = await _read_cached_token(redis)
    if cached and UpstoxAPI.validate_token({"access_token": cached}):
        await redis.set(K.SYSTEM_HEALTH_AUTH, "valid")
        return AuthResult(ok=True, reason="valid", token=cached, refreshed=False)

    if allow_playwright:
        logger.info("auth_bootstrap: cached token invalid; attempting Playwright login")
        new_token = await _refresh_via_playwright(redis, creds, headless=headless)
        if new_token:
            return AuthResult(ok=True, reason="valid", token=new_token, refreshed=True)
        logger.warning("auth_bootstrap: Playwright path failed; falling back to v3 webhook")

    res = UpstoxAPI.request_access_token(
        {"creds": {"api_key": creds.get("api_key"), "api_secret": creds.get("api_secret")}}
    )
    if res["success"]:
        logger.info(
            f"auth_bootstrap: v3 token request issued; "
            f"notifier_url={res['data'].get('notifier_url')!r} "
            f"expiry={res['data'].get('authorization_expiry')!r}"
        )
    else:
        logger.warning(f"auth_bootstrap: v3 token request failed: {res['error']!r}")
    await redis.set(K.SYSTEM_HEALTH_AUTH, "invalid")
    return AuthResult(ok=False, reason="invalid", token=None, refreshed=False)
