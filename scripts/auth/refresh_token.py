"""
scripts/refresh_token.py — standalone token-refresh CLI.

Probes the cached token; if invalid, runs Playwright (mobile → TOTP → PIN) and
exchanges the auth_code for a fresh access_token; persists the result back into
Redis (`user:auth:access_token` JSON shape).

Usage:
    python scripts/refresh_token.py            # headless
    python scripts/refresh_token.py --headed   # show the browser (debugging)
    python scripts/refresh_token.py --probe    # probe only; don't refresh

Reads credentials from Redis (`user:credentials:upstox`), populated by
Init's hydrator. If Redis has no creds, falls back to `credentials.json`
at the repo root (Phase 0 setup file).

Exits 0 on success (token valid afterwards), 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import orjson
from loguru import logger

from brokers.upstox import UpstoxAPI
from engines.init.auth_bootstrap import (
    _read_cached_token,
    _read_creds,
    _refresh_via_playwright,
    persist_token,
)
from log_setup import configure
from state import keys as K
from state import redis_client


def _load_fallback_creds() -> dict | None:
    """If Redis has no credentials yet, fall back to the repo-root credentials.json."""
    candidates = [
        Path("/home/ubuntu/credentials.json"),
        Path("/home/ubuntu/premium_diff_bot/credentials.json"),
        Path(__file__).resolve().parent.parent.parent / "credentials.json",
    ]
    for p in candidates:
        if p.is_file():
            data = json.loads(p.read_text())
            up = data.get("upstox") or data
            return up
    return None


async def _amain(probe_only: bool, headless: bool) -> int:
    configure(engine_name="refresh_token")
    redis_client.init_pools()
    redis = redis_client.get_redis()

    cached = await _read_cached_token(redis)
    valid = bool(cached) and UpstoxAPI.validate_token({"access_token": cached})
    logger.info(f"cached token present: {bool(cached)}, valid: {valid}")

    if valid:
        print("[refresh_token] cached token is VALID — no action needed.")
        return 0

    if probe_only:
        print("[refresh_token] cached token is INVALID; --probe set, not refreshing.")
        return 1

    creds = await _read_creds(redis)
    if not creds:
        logger.warning("no credentials in Redis; trying credentials.json fallback")
        creds = _load_fallback_creds()
        if creds:
            await redis.set(K.USER_CREDENTIALS_UPSTOX, orjson.dumps(creds))
            logger.info("seeded user:credentials:upstox from credentials.json")
    if not creds:
        logger.error("no credentials anywhere; cannot refresh")
        return 1

    new_token = await _refresh_via_playwright(redis, creds, headless=headless)
    if not new_token:
        logger.error("playwright refresh failed")
        return 1

    if not UpstoxAPI.validate_token({"access_token": new_token}):
        logger.error("refreshed token is rejected by /v2/user/profile")
        return 1

    await persist_token(redis, new_token, source="playwright")
    print(f"[refresh_token] OK — new token persisted (prefix={new_token[:30]}...)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true", help="probe only; don't refresh")
    p.add_argument("--headed", action="store_true", help="show the Playwright browser")
    args = p.parse_args()
    try:
        return asyncio.run(_amain(probe_only=args.probe, headless=not args.headed))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
