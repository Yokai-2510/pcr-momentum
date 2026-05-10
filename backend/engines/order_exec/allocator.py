"""
engines.order_exec.allocator — capital + concurrency reservation.

Atomic check-then-mutate using Redis WATCH/MULTI/EXEC via the
`redis-py` `transaction()` helper. If a concurrent client mutates any
watched key between the check and the EXEC, the transaction retries
(up to a small bounded count). With the system's worker pool of 8
and a global concurrency cap of 2, contention is rare.

Three caps enforced on `check_and_reserve`:
  1. per-index   : the index must not already have an open position
  2. global      : total open across all indexes must not exceed
                   `max_concurrent_positions`
  3. capital     : `deployed[total] + premium_required <= trading_capital_inr`

`release` is unconditional and idempotent — never refuses, never raises
on missing fields.
"""

from __future__ import annotations

import redis as _redis_sync
from loguru import logger

from state import keys as K

# Bounded retries for the optimistic-lock path. Each retry is a single RTT;
# 5 is generous given expected contention ≤ 2 concurrent workers.
_TXN_MAX_RETRIES = 5


def check_and_reserve(
    redis_sync: _redis_sync.Redis,
    *,
    index: str,
    premium_required_inr: float,
    trading_capital_inr: float,
    max_concurrent_positions: int,
) -> tuple[bool, str, float, int]:
    """Atomically check the three caps and, if they pass, reserve the slot.

    Returns ``(ok, reason, deployed_total_after, open_total_after)``.

    On ``ok=True`` the reservation is held until ``release(...)`` is called.
    On ``ok=False`` no state has been mutated and `reason` identifies the
    cap that failed: ``ALREADY_OPEN_ON_INDEX`` / ``MAX_CONCURRENT_REACHED`` /
    ``INSUFFICIENT_CAPITAL``.
    """
    log = logger.bind(engine="order_exec", index=index)
    deployed_key = K.ORDERS_ALLOCATOR_DEPLOYED
    open_key = K.ORDERS_ALLOCATOR_OPEN_COUNT
    symbols_key = K.ORDERS_ALLOCATOR_OPEN_SYMBOLS

    result: dict[str, float | int | str | bool] = {"ok": False, "reason": "unknown"}

    def _txn(pipe: _redis_sync.client.Pipeline) -> None:
        # Cap 1: per-index
        if pipe.sismember(symbols_key, index):
            dep = float(pipe.hget(deployed_key, "total") or 0)
            cnt = int(pipe.hget(open_key, "total") or 0)
            result.update(ok=False, reason="ALREADY_OPEN_ON_INDEX",
                          deployed_after=dep, open_after=cnt)
            pipe.unwatch()
            return

        cur_total = int(pipe.hget(open_key, "total") or 0)
        deployed_total = float(pipe.hget(deployed_key, "total") or 0)

        # Cap 2: global concurrency
        if cur_total + 1 > max_concurrent_positions:
            result.update(ok=False, reason="MAX_CONCURRENT_REACHED",
                          deployed_after=deployed_total, open_after=cur_total)
            pipe.unwatch()
            return

        # Cap 3: capital
        if deployed_total + premium_required_inr > trading_capital_inr:
            result.update(ok=False, reason="INSUFFICIENT_CAPITAL",
                          deployed_after=deployed_total, open_after=cur_total)
            pipe.unwatch()
            return

        # Reserve. Buffered until EXEC.
        pipe.multi()
        pipe.hincrbyfloat(deployed_key, index, premium_required_inr)
        pipe.hincrbyfloat(deployed_key, "total", premium_required_inr)
        pipe.hincrby(open_key, index, 1)
        pipe.hincrby(open_key, "total", 1)
        pipe.sadd(symbols_key, index)

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
                deployed_key, open_key, symbols_key,
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
    index: str,
    premium_to_release_inr: float,
) -> tuple[bool, str]:
    """Release a previously-held reservation.

    Unconditional; never refuses. If a release is called twice or after
    a manual reset, the counters may go negative — callers (cleanup path)
    are expected to call this exactly once per successful reserve.
    """
    log = logger.bind(engine="order_exec", index=index)
    deployed_key = K.ORDERS_ALLOCATOR_DEPLOYED
    open_key = K.ORDERS_ALLOCATOR_OPEN_COUNT
    symbols_key = K.ORDERS_ALLOCATOR_OPEN_SYMBOLS

    try:
        pipe = redis_sync.pipeline(transaction=True)
        pipe.hincrbyfloat(deployed_key, index, -float(premium_to_release_inr))
        pipe.hincrbyfloat(deployed_key, "total", -float(premium_to_release_inr))
        pipe.hincrby(open_key, index, -1)
        pipe.hincrby(open_key, "total", -1)
        pipe.srem(symbols_key, index)
        pipe.execute()
    except Exception as e:
        log.exception(f"allocator release raised: {e!r}")
        return False, "ALLOCATOR_ERROR"
    return True, "OK"
