"""
engines.order_exec.allocator — atomic capital + concurrency reservation.

Wraps the two Lua scripts under `state/lua/`:
  - capital_allocator_check_and_reserve.lua
  - capital_allocator_release.lua

The pair guarantees that two concurrent worker threads cannot both succeed
when capital is only sufficient for one, and that the per-index
`max_concurrent_positions=1` rule + global `=2` rule from HLD §4.4 are
enforced atomically inside Redis.

Single-flight: scripts are loaded via `register_script(source)` against the
caller's redis client, which is what fakeredis expects in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import redis as _redis_sync
from loguru import logger

from state import keys as K

_LUA_DIR = Path(__file__).resolve().parents[2] / "state" / "lua"

_check_source: str | None = None
_release_source: str | None = None


def _read_lua(name: str) -> str:
    path = _LUA_DIR / f"{name}.lua"
    if not path.is_file():
        raise FileNotFoundError(f"missing lua: {path}")
    return path.read_text(encoding="utf-8")


def _check_script(redis_sync: _redis_sync.Redis) -> Any:
    global _check_source
    if _check_source is None:
        _check_source = _read_lua("capital_allocator_check_and_reserve")
    return redis_sync.register_script(_check_source)


def _release_script(redis_sync: _redis_sync.Redis) -> Any:
    global _release_source
    if _release_source is None:
        _release_source = _read_lua("capital_allocator_release")
    return redis_sync.register_script(_release_source)


def _allocator_keys() -> list[str]:
    return [
        K.ORDERS_ALLOCATOR_DEPLOYED,
        K.ORDERS_ALLOCATOR_OPEN_COUNT,
        K.ORDERS_ALLOCATOR_OPEN_SYMBOLS,
    ]


def check_and_reserve(
    redis_sync: _redis_sync.Redis,
    *,
    index: str,
    premium_required_inr: float,
    trading_capital_inr: float,
    max_concurrent_positions: int,
) -> tuple[bool, str, float, int]:
    """Run the allocator gate. Returns (ok, reason, deployed_after, open_after).

    On `ok=True`, the reservation is held until `release(...)` is called by
    the caller (cleanup path on success, or abort path on entry failure).
    """
    log = logger.bind(engine="order_exec", index=index)
    script = _check_script(redis_sync)
    try:
        raw = script(
            keys=_allocator_keys(),
            args=[
                index,
                f"{premium_required_inr:.4f}",
                f"{trading_capital_inr:.4f}",
                str(int(max_concurrent_positions)),
            ],
            client=redis_sync,
        )
    except Exception as e:
        log.exception(f"allocator check_and_reserve raised: {e!r}")
        return False, "allocator_lua_error", 0.0, 0

    ok_flag = int(raw[0]) if isinstance(raw, list | tuple) else 0
    reason = (
        raw[1].decode() if isinstance(raw[1], bytes) else str(raw[1])
        if isinstance(raw, list | tuple) and len(raw) >= 2 else "unknown"
    )
    deployed_after = float(raw[2]) if isinstance(raw, list | tuple) and len(raw) >= 3 else 0.0
    open_after = int(raw[3]) if isinstance(raw, list | tuple) and len(raw) >= 4 else 0
    return ok_flag == 1, reason, deployed_after, open_after


def release(
    redis_sync: _redis_sync.Redis,
    *,
    index: str,
    premium_to_release_inr: float,
) -> tuple[bool, str]:
    """Release a previously-held reservation. Idempotent (no-op if already released)."""
    log = logger.bind(engine="order_exec", index=index)
    script = _release_script(redis_sync)
    try:
        raw = script(
            keys=_allocator_keys(),
            args=[index, f"{premium_to_release_inr:.4f}"],
            client=redis_sync,
        )
    except Exception as e:
        log.exception(f"allocator release raised: {e!r}")
        return False, "allocator_lua_error"
    ok_flag = int(raw[0]) if isinstance(raw, list | tuple) else 0
    reason = (
        raw[1].decode() if isinstance(raw[1], bytes) else str(raw[1])
        if isinstance(raw, list | tuple) and len(raw) >= 2 else "unknown"
    )
    return ok_flag == 1, reason
