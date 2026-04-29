"""
engines.init.holiday_check — daily go/no-go gate (Sequential_Flow §7 step 7).

Two layers:
  1. Local (Redis cache populated by hydrator): is today's ISO date in
     `system:scheduler:market_calendar:trading_days`?
  2. Broker (live REST): UpstoxAPI.get_holidays + is_holiday_for predicate.

`is_trading_day_today` is the synchronous predicate used by Init main(); it
prefers Redis cache and falls back to broker REST only if cache is empty.
"""

from __future__ import annotations

from datetime import date

import redis as _redis_sync

from brokers.upstox import UpstoxAPI
from state import keys as K


def _today_iso() -> str:
    return date.today().isoformat()


def is_trading_day_today_via_cache(redis_sync: _redis_sync.Redis) -> bool | None:
    """Return True/False from Redis cache, or None if cache is empty (== unknown)."""
    has_trading = redis_sync.exists(K.SYSTEM_SCHEDULER_TRADING_DAYS)
    has_holidays = redis_sync.exists(K.SYSTEM_SCHEDULER_HOLIDAYS)
    if not has_trading and not has_holidays:
        return None  # cache not populated
    today = _today_iso()
    if redis_sync.sismember(K.SYSTEM_SCHEDULER_TRADING_DAYS, today):
        return True
    if redis_sync.sismember(K.SYSTEM_SCHEDULER_HOLIDAYS, today):
        return False
    # Date not in either set — calendar didn't cover today; treat as unknown.
    return None


def is_trading_day_today_via_broker(access_token: str) -> bool:
    """Live broker probe — fail-CLOSED on REST failure (treat as holiday)."""
    res = UpstoxAPI.get_holidays({"access_token": access_token})
    if not res["success"]:
        return False  # fail-closed
    today = _today_iso()
    today_entries = [e for e in (res["data"] or []) if e.get("date") == today]
    if not today_entries:
        # No entry for today → not a holiday → trading day
        return True
    # Entry exists → is NSE in closed_exchanges? predicate says yes ⇒ holiday
    return not UpstoxAPI.is_holiday_for(today_entries, "NSE")


def is_trading_day_today(redis_sync: _redis_sync.Redis, access_token: str | None = None) -> bool:
    """Combined gate: cache first, broker fallback. Returns True iff today is
    a trading day."""
    cached = is_trading_day_today_via_cache(redis_sync)
    if cached is not None:
        return cached
    if access_token:
        return is_trading_day_today_via_broker(access_token)
    # Cache empty AND no token → fail-closed: assume holiday so the day is skipped.
    return False
