"""
engines.init.main — the 12-step boot sequence.

Per docs/Sequential_Flow.md §7 (the canonical decision tree). This module is
`Type=oneshot` under systemd; on success the cyclic stack target activates.

Exit codes:
  0  — Init succeeded OR the day is correctly skipped (no infra failure).
       systemd OnSuccess fires only if `system:flags:trading_active=true`,
       which Init ONLY sets when all checks pass. So the "skip the day"
       outcomes also exit 0 (they just leave trading_active=false).
  1  — infra failure (Redis/Postgres unreachable, template apply failed,
       hydrator crashed, etc.). systemd alerts.

Run:
    python -m engines.init
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import date
from typing import cast

import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from engines.init import (
    auth_bootstrap,
    holiday_check,
    instruments_loader,
    postgres_hydrator,
    redis_template,
    strike_basket_builder,
)
from log_setup import configure
from state import keys as K
from state import postgres_client, redis_client
from state.config_loader import get_settings


async def _read_str(redis: _redis_async.Redis, key: str, default: str = "") -> str:
    raw = await redis.get(key)
    if raw is None:
        return default
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def _set_disabled(redis: _redis_async.Redis, reason: str) -> None:
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, reason)
    await pipe.execute()


async def _set_init_failed(redis: _redis_async.Redis, reason: str) -> None:
    await redis.set(K.SYSTEM_FLAGS_INIT_FAILED, reason)
    await _set_disabled(redis, "init_failed")


async def main() -> int:
    configure(engine_name="init")
    log = logger.bind(engine="init")

    settings = get_settings()

    # ── STEP 1: Connect Redis ───────────────────────────────────────────
    try:
        redis_client.init_pools()
        redis = redis_client.get_redis()
        await redis.ping()  # type: ignore[misc]
    except Exception as e:
        log.error(f"step1: redis connect failed: {e}")
        return 1
    log.info("step1: redis OK")

    # ── STEP 2: Connect Postgres ────────────────────────────────────────
    try:
        await postgres_client.init_pool(settings.database_url)
        pool = postgres_client.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as e:
        log.error(f"step2: postgres connect failed: {e}")
        await _set_init_failed(redis, "postgres_connect_failed")
        return 1
    log.info("step2: postgres OK")

    # ── STEP 3: Apply canonical Redis template (FLUSH runtime first) ────
    try:
        counters = await redis_template.apply(redis, flush_runtime=True)
    except Exception as e:
        log.error(f"step3: template apply failed: {e}")
        await _set_init_failed(redis, "template_apply_failed")
        return 1
    log.info(
        f"step3: template applied — deleted={counters['deleted']} "
        f"written={counters['written']} skipped={counters['skipped']}"
    )

    # Lifecycle stamp
    await redis.set(K.SYSTEM_LIFECYCLE_START_TS, str(int(time.time() * 1000)))
    await redis.set(K.SYSTEM_LIFECYCLE_GIT_SHA, os.environ.get("GIT_SHA", ""))

    # ── STEP 4: Hydrate from Postgres ───────────────────────────────────
    try:
        hydration = await postgres_hydrator.hydrate_all(redis, pool)
    except Exception as e:
        log.error(f"step4: hydrator failed: {e}")
        await _set_init_failed(redis, "hydration_failed")
        return 1
    log.info(
        f"step4: hydrated — user={hydration.get('user', {}).get('username', '?') if hydration.get('user') else None} "
        f"creds_ok={hydration['creds_ok']} configs={hydration['configs']} "
        f"calendar={hydration['calendar']} session_set={hydration['session']}"
    )

    # ── STEP 5: User flags (auto_continue / skip_today) ─────────────────
    auto_continue = await _read_str(redis, "system:flags:auto_continue", "true")
    skip_today = await _read_str(redis, "system:flags:skip_today", "false")
    if auto_continue.lower() == "false":
        await _set_disabled(redis, "auto_continue_off")
        log.info("step5: auto_continue=false; skipping the day")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0
    if skip_today.lower() == "true":
        await redis.set("system:flags:skip_today", "false")
        await _set_disabled(redis, "skip_today")
        log.info("step5: skip_today consumed; skipping the day")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0
    log.info("step5: user flags OK")

    # ── STEP 6: Auth bootstrap ──────────────────────────────────────────
    if not hydration["creds_ok"]:
        log.warning("step6: credentials missing — stack idle until user submits")
        await _set_disabled(redis, "awaiting_credentials")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0
    auth_res = await auth_bootstrap.ensure_valid_token(redis)
    if not auth_res.ok:
        log.warning(f"step6: auth not valid (reason={auth_res.reason}); stack idle")
        await _set_disabled(
            redis,
            "awaiting_credentials" if auth_res.reason == "missing" else "auth_invalid",
        )
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0
    access_token: str = cast(str, auth_res.token)
    log.info("step6: auth OK")

    # ── STEP 7: Holiday gate (cache → broker fallback) ──────────────────
    redis_sync = redis_client.get_redis_sync()
    is_trading = holiday_check.is_trading_day_today(redis_sync, access_token=access_token)
    if not is_trading:
        await _set_disabled(redis, "holiday")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        log.info("step7: today is non-trading — stack idle")
        return 0
    log.info("step7: trading day confirmed")

    # ── STEP 8: Standard session check (broker timings) ─────────────────
    timings_res = UpstoxAPI.get_market_timings(
        {"date": date.today().isoformat(), "access_token": access_token}
    )
    if timings_res["success"]:
        if not UpstoxAPI.is_standard_session(timings_res["data"], "NSE"):
            log.warning(f"step8: non-standard NSE session today: {timings_res['data']!r}")
            await _set_disabled(redis, "non_standard_session")
            await redis.set(K.SYSTEM_FLAGS_READY, "true")
            return 0
    else:
        log.warning(
            f"step8: timings probe failed ({timings_res['error']!r}); proceeding with cache"
        )
    log.info("step8: NSE 09:15-15:30 session confirmed")

    # ── STEP 9: Instruments master refresh (best-effort) ────────────────
    try:
        n = await instruments_loader.load_master_instruments(redis)
    except Exception as e:
        log.warning(f"step9: instruments load raised; continuing: {e}")
        n = 0
    log.info(f"step9: instruments_loader wrote {n} rows")

    # ── STEP 10: Resolve effective mode ─────────────────────────────────
    force_paper = await _read_str(redis, "system:flags:force_paper_today", "false")
    mode_persistent = await _read_str(redis, K.SYSTEM_FLAGS_MODE, "paper")
    effective_mode = "paper" if force_paper.lower() == "true" else mode_persistent
    if force_paper.lower() == "true":
        await redis.set("system:flags:force_paper_today", "false")
    await redis.set("system:flags:mode_today", effective_mode)
    log.info(f"step10: mode={effective_mode}")

    # ── STEP 11: Per-index basket build ─────────────────────────────────
    enabled_indexes: list[K.IndexName] = []
    for idx in K.INDEXES:
        flag = await _read_str(redis, K.strategy_enabled(idx), "true")
        if flag.lower() == "true":
            enabled_indexes.append(idx)
    if not enabled_indexes:
        log.error("step11: no indexes enabled; skipping the day")
        await _set_disabled(redis, "no_indexes_enabled")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0

    succeeded = 0
    for active_idx in enabled_indexes:
        try:
            res = await strike_basket_builder.build_for_index(
                redis, active_idx, access_token=access_token
            )
        except Exception as e:
            log.error(f"step11[{active_idx}]: builder raised: {e}")
            await redis.set(K.strategy_enabled(active_idx), "false")
            continue
        if "error" in res:
            log.warning(f"step11[{active_idx}]: failed: {res['error']}")
            continue
        log.info(
            f"step11[{active_idx}]: atm={res['atm']} expiry={res['expiry']} tokens={res['tokens']}"
        )
        succeeded += 1
    if succeeded == 0:
        log.error("step11: all indexes failed; skipping the day")
        await _set_disabled(redis, "basket_build_failed")
        await redis.set(K.SYSTEM_FLAGS_READY, "true")
        return 0

    # ── STEP 12: Final readiness ────────────────────────────────────────
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.SYSTEM_FLAGS_READY, "true")
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "none")
    pipe.publish(K.SYSTEM_PUB_SYSTEM_EVENT, '{"event":"ready"}')
    await pipe.execute()
    log.info(f"step12: init complete — {succeeded}/{len(enabled_indexes)} indexes ready")
    return 0


def _entrypoint() -> int:
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(f"init: unhandled exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_entrypoint())
