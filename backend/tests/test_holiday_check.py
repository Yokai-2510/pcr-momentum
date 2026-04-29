"""init.holiday_check — predicate against fakeredis sync."""

from __future__ import annotations

from datetime import date

import fakeredis
import pytest

from engines.init import holiday_check
from state import keys as K


@pytest.fixture
def redis_sync():
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    yield r
    r.close()


def test_cache_empty_returns_none(redis_sync) -> None:
    assert holiday_check.is_trading_day_today_via_cache(redis_sync) is None


def test_today_in_trading_days_set(redis_sync) -> None:
    redis_sync.sadd(K.SYSTEM_SCHEDULER_TRADING_DAYS, date.today().isoformat())
    assert holiday_check.is_trading_day_today_via_cache(redis_sync) is True


def test_today_in_holidays_set(redis_sync) -> None:
    redis_sync.sadd(K.SYSTEM_SCHEDULER_HOLIDAYS, date.today().isoformat())
    assert holiday_check.is_trading_day_today_via_cache(redis_sync) is False


def test_today_in_neither_returns_none(redis_sync) -> None:
    redis_sync.sadd(K.SYSTEM_SCHEDULER_TRADING_DAYS, "2099-01-01")
    redis_sync.sadd(K.SYSTEM_SCHEDULER_HOLIDAYS, "2099-12-25")
    assert holiday_check.is_trading_day_today_via_cache(redis_sync) is None


def test_combined_cache_first_no_token(redis_sync) -> None:
    redis_sync.sadd(K.SYSTEM_SCHEDULER_HOLIDAYS, date.today().isoformat())
    assert holiday_check.is_trading_day_today(redis_sync, access_token=None) is False


def test_combined_no_cache_no_token_fail_closed(redis_sync) -> None:
    # Empty cache + no token ⇒ False (skip the day)
    assert holiday_check.is_trading_day_today(redis_sync, access_token=None) is False
