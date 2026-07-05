"""Tests for engines.order_exec.allocator (pure-Python; WATCH/MULTI/EXEC).

Each test exercises both the reserve and release paths so the symmetry
stays correct. Reservations are attributed per-vessel (strategy_id, index);
the hashes carry vessel / strategy-subtotal / total fields.
"""

from __future__ import annotations

from typing import Any

from engines.order_exec import allocator
from state import keys as K

SID = "bid_ask_imbalance_v1"
SID2 = "other_strategy_v1"


_SIG_SEQ = iter(range(1, 10_000))


def _reserve(
    redis: Any,
    sid: str = SID,
    index: str = "nifty50",
    sig_id: str | None = None,
    token: str = "NSE_FO|49520",
    **kw: Any,
) -> tuple:
    params: dict[str, Any] = {
        "premium_required_inr": 10_000.0,
        "trading_capital_inr": 200_000.0,
        "max_concurrent_positions": 2,
    }
    params.update(kw)
    return allocator.check_and_reserve(
        redis,
        strategy_id=sid,
        index=index,
        sig_id=sig_id or f"sig-{next(_SIG_SEQ)}",
        instrument_token=token,
        **params,
    )


def _release(
    redis: Any,
    sid: str = SID,
    index: str = "nifty50",
    sig_id: str = "sig-x",
    token: str = "NSE_FO|49520",
    premium: float = 10_000.0,
) -> tuple:
    return allocator.release(
        redis,
        strategy_id=sid,
        index=index,
        sig_id=sig_id,
        instrument_token=token,
        premium_to_release_inr=premium,
    )


def test_reserve_then_release_roundtrips(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, reason, dep, cnt = _reserve(redis, sig_id="sig-a")
    assert ok is True
    assert reason == "OK"
    assert dep == 10_000.0
    assert cnt == 1

    # Strategy subtotal + vessel fields tracked.
    assert float(redis.hget(K.ORDERS_ALLOCATOR_DEPLOYED, f"strategy:{SID}")) == 10_000.0
    assert float(redis.hget(K.ORDERS_ALLOCATOR_DEPLOYED, f"{SID}:nifty50")) == 10_000.0

    ok2, reason2 = _release(redis, sig_id="sig-a")
    assert ok2 is True
    assert reason2 == "OK"
    assert float(redis.hget(K.ORDERS_ALLOCATOR_DEPLOYED, f"strategy:{SID}")) == 0.0

    # After release the allocator slot is free again.
    ok3, reason3, _dep3, _cnt3 = _reserve(redis)
    assert ok3 is True
    assert reason3 == "OK"


def test_reserve_blocks_already_open_on_vessel(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, _r, _d, _c = _reserve(redis)
    assert ok is True

    # Different token, same vessel, default cap of 1 -> vessel cap.
    ok2, reason2, _d2, _c2 = _reserve(redis, token="NSE_FO|other")
    assert ok2 is False
    assert reason2 == "ALREADY_OPEN_ON_VESSEL"


def test_duplicate_signal_and_duplicate_token_blocked(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, _, _, _ = _reserve(redis, sig_id="sig-dup", token="NSE_FO|111")
    assert ok is True

    # Exact same sig_id -> replay protection.
    ok2, reason2, _, _ = _reserve(redis, sig_id="sig-dup", token="NSE_FO|111")
    assert ok2 is False and reason2 == "DUPLICATE_SIGNAL"

    # Same option token, new sig, even with vessel headroom -> no doubled leg.
    ok3, reason3, _, _ = _reserve(
        redis, token="NSE_FO|111", max_positions_per_vessel=5, max_concurrent_positions=5
    )
    assert ok3 is False and reason3 == "ALREADY_OPEN_ON_TOKEN"


def test_multi_position_vessel_cap(fake_redis_sync: Any) -> None:
    """Stock-universe vessels hold several positions up to their cap."""
    redis = fake_redis_sync
    kw = {"max_positions_per_vessel": 2, "max_concurrent_positions": 5}
    ok1, r1, _, _ = _reserve(redis, index="nifty50_stocks", token="NSE_FO|A", **kw)
    ok2, r2, _, c2 = _reserve(redis, index="nifty50_stocks", token="NSE_FO|B", **kw)
    assert ok1 and ok2, (r1, r2)
    assert c2 == 2
    ok3, reason3, _, _ = _reserve(redis, index="nifty50_stocks", token="NSE_FO|C", **kw)
    assert ok3 is False and reason3 == "ALREADY_OPEN_ON_VESSEL"


def test_two_strategies_can_hold_same_instrument(fake_redis_sync: Any) -> None:
    """Per-vessel cap is (strategy, instrument) — NOT per instrument."""
    redis = fake_redis_sync
    ok, _, _, _ = _reserve(redis, sid=SID, index="nifty50")
    assert ok is True
    ok2, reason2, _, cnt2 = _reserve(redis, sid=SID2, index="nifty50")
    assert ok2 is True, reason2
    assert cnt2 == 2


def test_reserve_blocks_max_concurrent(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    # Global concurrency cap of 1: first vessel reserves, second is blocked.
    ok, _, _, _ = _reserve(redis, index="nifty50", max_concurrent_positions=1)
    assert ok is True

    ok2, reason2, _, _ = _reserve(redis, index="banknifty", max_concurrent_positions=1)
    assert ok2 is False
    assert reason2 == "MAX_CONCURRENT_REACHED"


def test_reserve_blocks_insufficient_capital(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    ok, reason, _, _ = _reserve(
        redis, premium_required_inr=300_000.0, trading_capital_inr=200_000.0
    )
    assert ok is False
    assert reason == "INSUFFICIENT_CAPITAL"


def test_strategy_parallel_cap(fake_redis_sync: Any) -> None:
    """A strategy's own parallel cap binds before the global cap."""
    redis = fake_redis_sync
    ok, _, _, _ = _reserve(
        redis, index="nifty50", strategy_max_parallel=1, max_concurrent_positions=5
    )
    assert ok is True

    # Same strategy, different instrument: blocked by the strategy cap.
    ok2, reason2, _, _ = _reserve(
        redis, index="banknifty", strategy_max_parallel=1, max_concurrent_positions=5
    )
    assert ok2 is False
    assert reason2 == "MAX_PARALLEL_FOR_STRATEGY"

    # A DIFFERENT strategy is not affected.
    ok3, reason3, _, _ = _reserve(
        redis, sid=SID2, index="banknifty", strategy_max_parallel=1, max_concurrent_positions=5
    )
    assert ok3 is True, reason3


def test_strategy_capital_cap(fake_redis_sync: Any) -> None:
    """A strategy's own capital allocation binds before global capital."""
    redis = fake_redis_sync
    ok, _, _, _ = _reserve(
        redis,
        index="nifty50",
        premium_required_inr=40_000.0,
        strategy_capital_inr=50_000.0,
        max_concurrent_positions=5,
    )
    assert ok is True

    # 40k already deployed for this strategy; another 40k exceeds its 50k cap.
    ok2, reason2, _, _ = _reserve(
        redis,
        index="banknifty",
        premium_required_inr=40_000.0,
        strategy_capital_inr=50_000.0,
        max_concurrent_positions=5,
    )
    assert ok2 is False
    assert reason2 == "INSUFFICIENT_STRATEGY_CAPITAL"

    # The other strategy still has the global envelope available.
    ok3, reason3, _, _ = _reserve(
        redis,
        sid=SID2,
        index="banknifty",
        premium_required_inr=40_000.0,
        strategy_capital_inr=50_000.0,
        max_concurrent_positions=5,
    )
    assert ok3 is True, reason3


def test_zero_strategy_caps_mean_unlimited(fake_redis_sync: Any) -> None:
    """strategy caps of 0 fall through to the global envelope only."""
    redis = fake_redis_sync
    ok, reason, _, _ = _reserve(
        redis, strategy_capital_inr=0.0, strategy_max_parallel=0, max_concurrent_positions=5
    )
    assert ok is True, reason


def test_release_is_idempotent(fake_redis_sync: Any) -> None:
    redis = fake_redis_sync
    # Release with no reservation → NOT_RESERVED, ok=False
    ok, reason = _release(redis, sig_id="sig-none")
    assert ok is False
    assert reason == "NOT_RESERVED"

    # Reserve, then double-release: second is no-op.
    _reserve(redis, sig_id="sig-b")
    ok1, _ = _release(redis, sig_id="sig-b")
    ok2, reason2 = _release(redis, sig_id="sig-b")
    assert ok1 is True
    assert ok2 is False
    assert reason2 == "NOT_RESERVED"

    # Counters back to zero, not negative.
    assert int(redis.hget(K.ORDERS_ALLOCATOR_OPEN_COUNT, "total")) == 0
    assert float(redis.hget(K.ORDERS_ALLOCATOR_DEPLOYED, "total")) == 0.0
