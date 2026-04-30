"""
engines.order_exec.cleanup — Stage F (atomic Redis teardown).

Wraps the `cleanup_position.lua` script. Single EVALSHA round-trip removes:
  - orders:positions:{pos_id}                  (HASH)
  - orders:status:{pos_id}                     (HASH)
  - strategy:signals:{sig_id}                  (STRING)
  - orders:orders:{order_id} for entry + exit  (HASH)
  - orders:broker:pos:{order_id} for each      (HASH)
  - orders:broker:open_orders                  (SET removals)
  - strategy:{idx}:current_position_id         (STRING)
  - orders:positions:open                      (SET — drop pos_id)
  - orders:positions:open_by_index:{idx}       (SET — drop pos_id)
  - strategy:signals:active                    (SET — drop sig_id)
  - orders:positions:closed_today              (SET — add pos_id)
"""

from __future__ import annotations

import redis as _redis_sync
from loguru import logger

from state import keys as K
from state import redis_client


def cleanup(
    redis_sync: _redis_sync.Redis,
    *,
    pos_id: str,
    sig_id: str,
    order_ids: list[str],
    index: str,
) -> int:
    """Run the `cleanup_position` Lua script. Returns count of keys deleted."""
    script = redis_client.load_script("cleanup_position")
    keys = [
        K.orders_position(pos_id),                       # KEYS[1]
        K.ORDERS_POSITIONS_OPEN,                         # KEYS[2]
        K.orders_positions_open_by_index(index),         # KEYS[3]
        K.ORDERS_POSITIONS_CLOSED_TODAY,                 # KEYS[4]
        K.orders_status(pos_id),                         # KEYS[5]
        K.strategy_current_position_id(index),           # KEYS[6]
        K.STRATEGY_SIGNALS_ACTIVE,                       # KEYS[7]
        K.strategy_signal(sig_id),                       # KEYS[8]
    ]
    args = [pos_id, sig_id] + [str(o) for o in order_ids if o]
    try:
        n = int(script(keys=keys, args=args, client=redis_sync))
    except Exception as e:
        logger.exception(f"cleanup_position lua failed: {e!r}")
        raise
    logger.info(f"cleanup[{index}/{pos_id}]: lua deleted={n}")
    return n
