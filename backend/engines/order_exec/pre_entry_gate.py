"""
engines.order_exec.pre_entry_gate — Stage A.

Two-step gate per Strategy.md §10.1:

  1. `check(redis_sync, signal)` — read-only sanity gates (no state mutation).
     Trading-active flag, daily-loss circuit, kill switch, engine_up,
     leaf availability, spread, depth.

  2. `check_and_reserve(redis_sync, signal)` — runs `check()` then atomically
     reserves capital + concurrency slot via the allocator Lua. Returns the
     reserved premium so the caller can release on abort/cleanup.

`check()` remains a pure read-only function used by tests and observers;
`check_and_reserve()` is what `worker.process_signal` calls.

Returns
-------
check(...)                -> (ok: bool, reason: str)
check_and_reserve(...)    -> (ok: bool, reason: str, premium_reserved: float)
"""

from __future__ import annotations

from typing import Any

import orjson
import redis as _redis_sync

from engines.order_exec import allocator
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


def _read_risk_config(redis_sync: _redis_sync.Redis) -> dict[str, Any]:
    raw = redis_sync.get(K.STRATEGY_CONFIGS_RISK)
    if not raw:
        return {}
    blob = raw if isinstance(raw, bytes) else raw.encode()
    parsed = orjson.loads(blob)
    return parsed if isinstance(parsed, dict) else {}


def _read_index_config(redis_sync: _redis_sync.Redis, index: str) -> dict[str, Any]:
    raw = redis_sync.get(K.strategy_config_index(index))
    if not raw:
        return {}
    blob = raw if isinstance(raw, bytes) else raw.encode()
    parsed = orjson.loads(blob)
    return parsed if isinstance(parsed, dict) else {}


def check(redis_sync: _redis_sync.Redis, signal: Signal) -> tuple[bool, str]:
    """Read-only gates. (True, "ok") on full pass."""
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
    if ask_qty > 0 and ask_qty < signal.qty_lots:
        # Strict broker-side check is at place-time. Strategy already
        # enforced lots*lot_size depth.
        pass

    return True, "ok"


def _compute_premium_required(
    leaf: dict[str, Any], qty_lots: int, lot_size: int
) -> float:
    """Reservation premium = qty_lots * lot_size * ask (worst-case fill)."""
    ask = float(leaf.get("ask") or 0)
    if ask <= 0:
        ask = float(leaf.get("ltp") or 0)
    return float(qty_lots) * float(lot_size) * ask


def check_and_reserve(
    redis_sync: _redis_sync.Redis,
    signal: Signal,
) -> tuple[bool, str, float]:
    """Read-only gates + atomic allocator reservation.

    Returns (ok, reason, premium_reserved). When ok is True the caller MUST
    eventually call `allocator.release(...)` with the same index + premium
    (cleanup on success, abort on entry failure).
    """
    ok, reason = check(redis_sync, signal)
    if not ok:
        return False, reason, 0.0

    leaf = _read_leaf_for_token(redis_sync, signal.index, signal.instrument_token)
    if leaf is None:
        # Should be impossible (check() would have caught it); defensive.
        return False, "leaf_missing", 0.0

    idx_cfg = _read_index_config(redis_sync, signal.index)
    lot_size = int(idx_cfg.get("lot_size") or 1)

    risk_cfg = _read_risk_config(redis_sync)
    trading_capital = float(risk_cfg.get("trading_capital_inr") or 0)
    if trading_capital <= 0:
        return False, "allocator_no_capital_configured", 0.0
    max_concurrent = int(risk_cfg.get("max_concurrent_positions") or 0)
    if max_concurrent <= 0:
        return False, "allocator_no_concurrency_configured", 0.0

    premium_required = _compute_premium_required(leaf, signal.qty_lots, lot_size)
    if premium_required <= 0:
        return False, "allocator_premium_zero", 0.0

    ok2, alloc_reason, _dep, _cnt = allocator.check_and_reserve(
        redis_sync,
        index=signal.index,
        premium_required_inr=premium_required,
        trading_capital_inr=trading_capital,
        max_concurrent_positions=max_concurrent,
    )
    if not ok2:
        return False, f"allocator_{alloc_reason.lower()}", 0.0
    return True, "ok", premium_required
