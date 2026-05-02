"""Tests for engines.order_exec.allocator + the two Lua scripts.

Uses fakeredis-with-lua. Each test exercises both the reserve and release
paths so the symmetry stays correct.
"""

from __future__ import annotations

from typing import Any

from engines.order_exec import allocator


def test_reserve_then_release_roundtrips(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, reason, dep, cnt = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    assert ok is True
    assert reason == "OK"
    assert dep == 10_000.0
    assert cnt == 1

    ok2, reason2 = allocator.release(
        redis, index="nifty50", premium_to_release_inr=10_000.0,
    )
    assert ok2 is True
    assert reason2 == "OK"

    # After release the allocator slot is free again.
    ok3, reason3, _dep3, _cnt3 = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    assert ok3 is True
    assert reason3 == "OK"


def test_reserve_blocks_already_open_on_index(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, _r, _d, _c = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    assert ok is True

    ok2, reason2, _d2, _c2 = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    assert ok2 is False
    assert reason2 == "ALREADY_OPEN_ON_INDEX"


def test_reserve_blocks_max_concurrent(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    # Concurrency cap of 1: first index reserves, second is blocked.
    ok, _, _, _ = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=1,
    )
    assert ok is True

    ok2, reason2, _, _ = allocator.check_and_reserve(
        redis, index="banknifty", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=1,
    )
    assert ok2 is False
    assert reason2 == "MAX_CONCURRENT_REACHED"


def test_reserve_blocks_insufficient_capital(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, reason, _, _ = allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=300_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    assert ok is False
    assert reason == "INSUFFICIENT_CAPITAL"


def test_release_is_idempotent(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    # Release with no reservation → NOT_RESERVED, ok=False
    ok, reason = allocator.release(
        redis, index="nifty50", premium_to_release_inr=10_000.0,
    )
    assert ok is False
    assert reason == "NOT_RESERVED"

    # Reserve, then double-release: second is no-op.
    allocator.check_and_reserve(
        redis, index="nifty50", premium_required_inr=10_000.0,
        trading_capital_inr=200_000.0, max_concurrent_positions=2,
    )
    ok1, _ = allocator.release(
        redis, index="nifty50", premium_to_release_inr=10_000.0,
    )
    ok2, reason2 = allocator.release(
        redis, index="nifty50", premium_to_release_inr=10_000.0,
    )
    assert ok1 is True
    assert ok2 is False
    assert reason2 == "NOT_RESERVED"
