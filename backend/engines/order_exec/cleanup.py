"""
engines.order_exec.cleanup — final teardown of a closed position's
Redis footprint.

Sequential Python pipeline. Atomicity is not required — cleanup runs
from the single thread that owned the position, after the exit fill
has been confirmed. Any partial state on a mid-cleanup crash is wiped
by the next morning's init flush.

Touched keys (Schema.md §1.5):
  DEL  orders:positions:{pos_id}                  (HASH)
  DEL  orders:status:{pos_id}                     (HASH)
  DEL  strategy:signals:{sig_id}                  (STRING)
  DEL  orders:orders:{order_id}        (per id)   (HASH)
  DEL  orders:broker:pos:{order_id}    (per id)   (HASH)
  SREM orders:positions:open                      (SET, drop pos_id)
  SREM orders:positions:open_by_index:{idx}       (SET, drop pos_id)
  SREM orders:broker:open_orders       (per id)   (SET)
  SADD orders:positions:closed_today              (SET, add pos_id)
  SET  strategy:{sid}:{idx}:current_position_id   ""   (cleared)
"""

from __future__ import annotations

import redis as _redis_sync
from loguru import logger

from state import keys as K


def cleanup(
    redis_sync: _redis_sync.Redis,
    *,
    pos_id: str,
    sig_id: str,
    order_ids: list[str],
    index: str,
) -> int:
    """Run the per-position teardown. Returns count of DEL/SREM operations
    that touched something (best-effort metric; not consumed for control flow).
    """
    log = logger.bind(engine="order_exec", index=index, pos_id=pos_id)

    pipe = redis_sync.pipeline(transaction=False)
    pipe.delete(K.orders_position(pos_id))
    pipe.delete(K.orders_status(pos_id))
    pipe.delete(K.strategy_signal(sig_id))
    for oid in order_ids:
        if not oid:
            continue
        pipe.delete(K.orders_order(oid))
        pipe.delete(K.orders_broker_pos(oid))
        pipe.srem(K.ORDERS_BROKER_OPEN_ORDERS, oid)
    pipe.srem(K.ORDERS_POSITIONS_OPEN, pos_id)
    pipe.srem(K.orders_positions_open_by_index(index), pos_id)
    pipe.sadd(K.ORDERS_POSITIONS_CLOSED_TODAY, pos_id)
    pipe.set(K.strategy_current_position_id(index), "")

    try:
        results = pipe.execute()
    except Exception as e:
        log.exception(f"cleanup pipeline failed: {e!r}")
        return 0

    # Approximate count of "touched something" = sum of truthy results.
    touched = sum(1 for r in results if r)
    log.info(f"cleanup: ops={touched} order_ids={len([o for o in order_ids if o])}")
    return touched
