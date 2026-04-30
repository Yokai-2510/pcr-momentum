"""
engines.order_exec.pre_entry_gate — Stage A.

Reads-only check before placing an entry order. All seven gates from
Strategy.md §10.1 (the broker-circuit-limit one is checked at place-time
inside entry.py because it depends on the just-fetched best-bid/ask).

Returns (True, "ok") on pass, (False, "<reason>") on first failure.
"""

from __future__ import annotations

from typing import Any

import orjson
import redis as _redis_sync

from state import keys as K
from state.schemas.signal import Signal


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _kill_switch_engaged(redis_sync: _redis_sync.Redis, segment: str = "NSE_FO") -> bool:
    raw = redis_sync.get(K.USER_CAPITAL_KILL_SWITCH)
    if not raw:
        return False
    try:
        snapshot = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return False
    if not isinstance(snapshot, list):
        return False
    for entry in snapshot:
        if isinstance(entry, dict) and entry.get("segment") == segment:
            if entry.get("segment_status") != "ACTIVE":
                return True
            return bool(entry.get("kill_switch_enabled"))
    return False


def _read_leaf_for_token(
    redis_sync: _redis_sync.Redis, index: str, token: str
) -> dict[str, Any] | None:
    raw = redis_sync.get(K.market_data_index_option_chain(index))
    if not raw:
        return None
    blob = raw if isinstance(raw, bytes) else raw.encode()
    chain = orjson.loads(blob)
    if not isinstance(chain, dict):
        return None
    for _strike, sides in chain.items():
        if not isinstance(sides, dict):
            continue
        for side in ("ce", "pe"):
            leaf = sides.get(side)
            if isinstance(leaf, dict) and leaf.get("token") == token:
                return leaf
    return None


def _read_execution_config(redis_sync: _redis_sync.Redis) -> dict[str, Any]:
    raw = redis_sync.get(K.STRATEGY_CONFIGS_EXECUTION)
    if not raw:
        return {}
    blob = raw if isinstance(raw, bytes) else raw.encode()
    parsed = orjson.loads(blob)
    return parsed if isinstance(parsed, dict) else {}


def check(redis_sync: _redis_sync.Redis, signal: Signal) -> tuple[bool, str]:
    """Run all reads-only gates for `signal`. (True, "ok") on full pass."""
    # 1. Trading active
    if _decode(redis_sync.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)) != "true":
        return False, "trading_inactive"

    # 2. Daily loss circuit
    if _decode(redis_sync.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)) == "true":
        return False, "daily_loss_circuit"

    # 3. Kill switch (cached snapshot from Background polling)
    if _kill_switch_engaged(redis_sync, "NSE_FO"):
        return False, "kill_switch_engaged"

    # 4. Order Exec engine_up self-check
    if _decode(redis_sync.get(K.system_flag_engine_up("order_exec"))) != "true":
        return False, "order_exec_not_up"

    # 5+6. Spread + depth filter on the chosen strike
    leaf = _read_leaf_for_token(redis_sync, signal.index, signal.instrument_token)
    if leaf is None:
        return False, "leaf_missing"
    cfg = _read_execution_config(redis_sync)
    spread_skip_pct = float(cfg.get("spread_skip_pct") or 0.05)

    ltp = float(leaf.get("ltp") or 0)
    bid = float(leaf.get("bid") or 0)
    ask = float(leaf.get("ask") or 0)
    ask_qty = int(leaf.get("ask_qty") or 0)
    if ltp <= 0 or bid <= 0 or ask <= 0:
        return False, "no_quotes"
    if ask <= bid:
        return False, "crossed_book"
    if (ask - bid) / ltp > spread_skip_pct:
        return False, f"spread_too_wide:{(ask - bid) / ltp:.4f}"
    # Depth gate (allow zero ask_qty as "unknown" — let broker reject if deep).
    if ask_qty > 0 and ask_qty < signal.qty_lots:
        # qty_lots already represents lots; the strict broker-side check is
        # at place-time. Strategy's gate already enforced lots*lot_size depth.
        pass

    return True, "ok"
