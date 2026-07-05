"""
engines.order_exec.universe_lookup — resolve stock-option tokens via universes.

Signals from stock-universe strategies carry index="nifty50_stocks"; the
actual option leaf lives in the SYMBOL's chain (market_data:stk_{sym}:
option_chain), and per-symbol lot sizes live in the universe meta token_map.
The meta is static per session, so it's cached in-process with a short TTL.
"""

from __future__ import annotations

import time
from typing import Any

import orjson
import redis as _redis_sync

from state import keys as K

_META_TTL_SEC = 60.0
_meta_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _universe_meta(redis_sync: _redis_sync.Redis, index: str) -> dict[str, Any]:
    """Cached read of market_data:{index}:meta (empty dict when not a universe)."""
    now = time.monotonic()
    cached = _meta_cache.get(index)
    if cached and cached[0] > now:
        return cached[1]
    raw = redis_sync.get(K.market_data_index_meta(index))
    meta: dict[str, Any] = {}
    if raw:
        try:
            parsed = orjson.loads(raw if isinstance(raw, bytes) else str(raw).encode())
            if isinstance(parsed, dict) and isinstance(parsed.get("token_map"), dict):
                meta = parsed
        except Exception:
            meta = {}
    _meta_cache[index] = (now + _META_TTL_SEC, meta)
    return meta


def reset_cache_for_testing() -> None:
    _meta_cache.clear()


def token_meta(redis_sync: _redis_sync.Redis, index: str, token: str) -> dict[str, Any] | None:
    """{symbol, instrument_id, strike, side, lot_size} for a universe token."""
    meta = _universe_meta(redis_sync, index)
    entry = (meta.get("token_map") or {}).get(token)
    return entry if isinstance(entry, dict) else None


def read_leaf_via_universe(
    redis_sync: _redis_sync.Redis, index: str, token: str
) -> dict[str, Any] | None:
    """Find the option leaf for a universe token via its symbol's chain."""
    entry = token_meta(redis_sync, index, token)
    if not entry:
        return None
    sidx = entry.get("instrument_id")
    if not sidx:
        return None
    raw = redis_sync.get(K.market_data_index_option_chain(str(sidx)))
    if not raw:
        return None
    try:
        chain = orjson.loads(raw if isinstance(raw, bytes) else str(raw).encode())
    except Exception:
        return None
    if not isinstance(chain, dict):
        return None
    leaf = (chain.get(str(entry.get("strike"))) or {}).get(str(entry.get("side", "")).lower())
    return leaf if isinstance(leaf, dict) else None


def resolve_lot_size(redis_sync: _redis_sync.Redis, index: str, token: str, fallback: int) -> int:
    """Per-symbol lot size from the universe token_map; fallback = config value."""
    entry = token_meta(redis_sync, index, token)
    if entry and entry.get("lot_size"):
        return int(entry["lot_size"])
    return fallback
