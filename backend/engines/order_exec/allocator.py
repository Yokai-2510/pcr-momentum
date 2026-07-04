"""
engines.order_exec.allocator — capital + concurrency reservation.

Atomic check-then-mutate using Redis WATCH/MULTI/EXEC via the
`redis-py` `transaction()` helper. If a concurrent client mutates any
watched key between the check and the EXEC, the transaction retries
(up to a small bounded count). With the system's worker pool of 8
and a global concurrency cap of 2, contention is rare.

Multi-strategy accounting (Phase A): every reservation is attributed to a
VESSEL — a `(strategy_id, instrument)` pair. The deployed/open hashes carry
three granularities of fields:

    "{sid}:{idx}"      per-vessel amount / count
    "strategy:{sid}"   per-strategy subtotal
    "total"            global

Five caps enforced on `check_and_reserve` (any strategy-level cap set to 0
means "no per-strategy limit; only the global envelope applies"):

  1. per-vessel        : this (strategy, instrument) must not already hold
                         an open position
  2. strategy parallel : open positions for this strategy must stay under
                         `strategy_max_parallel`
  3. strategy capital  : strategy's deployed + premium <= `strategy_capital_inr`
  4. global concurrency: total open across everything must not exceed
                         `max_concurrent_positions`
  5. global capital    : `deployed[total] + premium <= trading_capital_inr`

`release` is idempotent — gated on the vessel's membership in the
open-symbols set, so double release never drives counters negative.
"""

from __future__ import annotations

import redis as _redis_sync
from loguru import logger

from state import keys as K

# Bounded retries for the optimistic-lock path. Each retry is a single RTT;
# 5 is generous given expected contention ≤ 2 concurrent workers.
_TXN_MAX_RETRIES = 5


def vessel_field(strategy_id: str, index: str) -> str:
    """Hash-field / set-entry name for one (strategy, instrument) vessel."""
    return f"{strategy_id}:{index}"


def strategy_field(strategy_id: str) -> str:
    """Hash-field name for a strategy's subtotal."""
    return f"strategy:{strategy_id}"


def check_and_reserve(
    redis_sync: _redis_sync.Redis,
    *,
    strategy_id: str,
    index: str,
    premium_required_inr: float,
    trading_capital_inr: float,
    max_concurrent_positions: int,
    strategy_capital_inr: float = 0.0,
    strategy_max_parallel: int = 0,
) -> tuple[bool, str, float, int]:
    """Atomically check the five caps and, if they pass, reserve the slot.

    Returns ``(ok, reason, deployed_total_after, open_total_after)``.

    On ``ok=True`` the reservation is held until ``release(...)`` is called.
    On ``ok=False`` no state has been mutated and `reason` identifies the
    cap that failed: ``ALREADY_OPEN_ON_VESSEL`` / ``MAX_PARALLEL_FOR_STRATEGY``
    / ``INSUFFICIENT_STRATEGY_CAPITAL`` / ``MAX_CONCURRENT_REACHED`` /
    ``INSUFFICIENT_CAPITAL``.

    `strategy_capital_inr` / `strategy_max_parallel` of 0 disable the
    respective per-strategy cap (global envelope still applies).
    """
    log = logger.bind(engine="order_exec", sid=strategy_id, index=index)
    deployed_key = K.ORDERS_ALLOCATOR_DEPLOYED
    open_key = K.ORDERS_ALLOCATOR_OPEN_COUNT
    symbols_key = K.ORDERS_ALLOCATOR_OPEN_SYMBOLS

    vfield = vessel_field(strategy_id, index)
    sfield = strategy_field(strategy_id)

    result: dict[str, float | int | str | bool] = {"ok": False, "reason": "unknown"}

    def _txn(pipe: _redis_sync.client.Pipeline) -> None:
        def _fail(reason: str, deployed: float, count: int) -> None:
            result.update(ok=False, reason=reason, deployed_after=deployed, open_after=count)
            pipe.unwatch()

        cur_total = int(pipe.hget(open_key, "total") or 0)
        deployed_total = float(pipe.hget(deployed_key, "total") or 0)

        # Cap 1: per-vessel — one open position per (strategy, instrument)
        if pipe.sismember(symbols_key, vfield):
            _fail("ALREADY_OPEN_ON_VESSEL", deployed_total, cur_total)
            return

        # Cap 2: per-strategy parallel positions
        if strategy_max_parallel > 0:
            strat_open = int(pipe.hget(open_key, sfield) or 0)
            if strat_open + 1 > strategy_max_parallel:
                _fail("MAX_PARALLEL_FOR_STRATEGY", deployed_total, cur_total)
                return

        # Cap 3: per-strategy capital
        if strategy_capital_inr > 0:
            strat_deployed = float(pipe.hget(deployed_key, sfield) or 0)
            if strat_deployed + premium_required_inr > strategy_capital_inr:
                _fail("INSUFFICIENT_STRATEGY_CAPITAL", deployed_total, cur_total)
                return

        # Cap 4: global concurrency
        if cur_total + 1 > max_concurrent_positions:
            _fail("MAX_CONCURRENT_REACHED", deployed_total, cur_total)
            return

        # Cap 5: global capital
        if deployed_total + premium_required_inr > trading_capital_inr:
            _fail("INSUFFICIENT_CAPITAL", deployed_total, cur_total)
            return

        # Reserve. Buffered until EXEC.
        pipe.multi()
        pipe.hincrbyfloat(deployed_key, vfield, premium_required_inr)
        pipe.hincrbyfloat(deployed_key, sfield, premium_required_inr)
        pipe.hincrbyfloat(deployed_key, "total", premium_required_inr)
        pipe.hincrby(open_key, vfield, 1)
        pipe.hincrby(open_key, sfield, 1)
        pipe.hincrby(open_key, "total", 1)
        pipe.sadd(symbols_key, vfield)

        result.update(
            ok=True,
            reason="OK",
            deployed_after=deployed_total + premium_required_inr,
            open_after=cur_total + 1,
        )

    for _attempt in range(_TXN_MAX_RETRIES):
        try:
            redis_sync.transaction(
                _txn,
                deployed_key,
                open_key,
                symbols_key,
                value_from_callable=False,
            )
            return (
                bool(result["ok"]),
                str(result["reason"]),
                float(result.get("deployed_after", 0.0)),  # type: ignore[arg-type]
                int(result.get("open_after", 0)),  # type: ignore[arg-type]
            )
        except _redis_sync.WatchError:
            # Another client mutated a watched key — retry.
            continue
        except Exception as e:
            log.exception(f"allocator check_and_reserve raised: {e!r}")
            return False, "ALLOCATOR_ERROR", 0.0, 0

    log.warning("allocator check_and_reserve: max retries hit")
    return False, "ALLOCATOR_RETRY_EXHAUSTED", 0.0, 0


def release(
    redis_sync: _redis_sync.Redis,
    *,
    strategy_id: str,
    index: str,
    premium_to_release_inr: float,
) -> tuple[bool, str]:
    """Release a previously-held reservation. Idempotent.

    Guards on the vessel's membership in the open-symbols set, so a double
    release (or a release when nothing is reserved) is a no-op returning
    ``(False, "NOT_RESERVED")`` instead of driving the counters negative.
    The cleanup path can therefore retry safely.
    """
    log = logger.bind(engine="order_exec", sid=strategy_id, index=index)
    deployed_key = K.ORDERS_ALLOCATOR_DEPLOYED
    open_key = K.ORDERS_ALLOCATOR_OPEN_COUNT
    symbols_key = K.ORDERS_ALLOCATOR_OPEN_SYMBOLS

    vfield = vessel_field(strategy_id, index)
    sfield = strategy_field(strategy_id)

    try:
        # SREM returns the count actually removed (0 or 1); doing it first
        # both gates idempotency and tells us whether a reservation existed.
        removed = redis_sync.srem(symbols_key, vfield)
        if not removed:
            return False, "NOT_RESERVED"
        pipe = redis_sync.pipeline(transaction=True)
        pipe.hincrbyfloat(deployed_key, vfield, -float(premium_to_release_inr))
        pipe.hincrbyfloat(deployed_key, sfield, -float(premium_to_release_inr))
        pipe.hincrbyfloat(deployed_key, "total", -float(premium_to_release_inr))
        pipe.hincrby(open_key, vfield, -1)
        pipe.hincrby(open_key, sfield, -1)
        pipe.hincrby(open_key, "total", -1)
        pipe.execute()
    except Exception as e:
        log.exception(f"allocator release raised: {e!r}")
        return False, "ALLOCATOR_ERROR"
    return True, "OK"
