"""
engines.strategy.pre_open_snapshot - Strategy.md section 4.3.

At 09:14:50 IST the strategy thread captures, per basket strike:
    pre_open_premium  (LTP)
    bid, ask
    oi                 (informational; not used in hot-path decisions)

The snapshot is written ONCE to `strategy:{index}:pre_open` (single JSON
SET) and then never mutated. After capture we validate the fail-closed gate:
    any basket strike with `ts == 0` (no pre-open trade) -> set
    `strategy:{index}:enabled = false`. The other index continues independently.
"""

from __future__ import annotations

from typing import Any

import orjson
import redis as _redis_sync
from loguru import logger

from state import keys as K


def _loads(raw: Any) -> Any:
    if isinstance(raw, bytes | bytearray | str):
        return orjson.loads(raw)
    return raw


def _read_chain(redis_sync: _redis_sync.Redis, index: str) -> dict[str, Any]:
    raw = redis_sync.get(K.market_data_index_option_chain(index))
    if not raw:
        return {}
    parsed = _loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _read_basket(redis_sync: _redis_sync.Redis, index: str) -> dict[str, list[str]]:
    raw = redis_sync.get(K.strategy_basket(index))
    if not raw:
        return {"ce": [], "pe": []}
    parsed = _loads(raw)
    if not isinstance(parsed, dict):
        return {"ce": [], "pe": []}
    ce_raw = parsed.get("ce")
    pe_raw = parsed.get("pe")
    ce = ce_raw if isinstance(ce_raw, list) else []
    pe = pe_raw if isinstance(pe_raw, list) else []
    return {"ce": [str(t) for t in ce], "pe": [str(t) for t in pe]}


def _leaf_for_token(chain: dict[str, Any], token: str) -> dict[str, Any] | None:
    """Find the {ce|pe} leaf in the option_chain whose token field matches."""
    for _strike, sides in chain.items():
        if not isinstance(sides, dict):
            continue
        for side in ("ce", "pe"):
            leaf = sides.get(side)
            if isinstance(leaf, dict) and leaf.get("token") == token:
                return leaf
    return None


def capture(
    redis_sync: _redis_sync.Redis, index: str, basket_tokens: list[str] | None = None
) -> dict[str, Any]:
    """Build + persist the immutable pre-open snapshot for `index`.

    Args:
        redis_sync: sync Redis client (Strategy is a sync thread).
        index: "nifty50" or "banknifty".
        basket_tokens: optional explicit token list; if None, read from
            `strategy:{index}:basket` (ce + pe combined).

    Returns:
        The snapshot dict written to Redis. On validation failure (any zero-ts
        token), returns `{"valid": False, "missing": [...], "snapshot": {...}}`
        and disables the index. On success, returns
        `{"valid": True, "snapshot": {...}}`.

    Idempotent: if `strategy:{index}:pre_open` already exists, the existing
    snapshot is returned untouched.
    """
    log = logger.bind(engine="strategy", index=index)

    existing_raw = redis_sync.get(K.strategy_pre_open(index))
    if existing_raw:
        existing = _loads(existing_raw)
        if not isinstance(existing, dict):
            existing = {}
        log.info(f"pre_open already captured ({len(existing)} strikes); reusing")
        return {"valid": True, "snapshot": existing, "reused": True}

    if basket_tokens is None:
        basket = _read_basket(redis_sync, index)
        basket_tokens = list(basket.get("ce", [])) + list(basket.get("pe", []))

    if not basket_tokens:
        log.error("pre_open_snapshot: empty basket; disabling index")
        redis_sync.set(K.strategy_enabled(index), "false")
        return {"valid": False, "snapshot": {}, "missing": []}

    chain = _read_chain(redis_sync, index)
    snapshot: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    for tok in basket_tokens:
        leaf = _leaf_for_token(chain, tok)
        if leaf is None or not leaf.get("ts"):
            missing.append(tok)
            snapshot[tok] = {
                "token": tok,
                "ltp": 0.0,
                "bid": 0.0,
                "ask": 0.0,
                "oi": 0,
                "ts": 0,
            }
            continue
        snapshot[tok] = {
            "token": tok,
            "ltp": float(leaf.get("ltp") or 0),
            "bid": float(leaf.get("bid") or 0),
            "ask": float(leaf.get("ask") or 0),
            "oi": int(leaf.get("oi") or 0),
            "ts": int(leaf.get("ts") or 0),
        }

    if missing:
        log.warning(
            f"pre_open_snapshot: {len(missing)}/{len(basket_tokens)} basket strikes "
            f"have ts=0; disabling index for the day. missing={missing[:3]}..."
        )
        redis_sync.set(K.strategy_enabled(index), "false")
        # We still persist the (invalid) snapshot for forensic inspection.
        redis_sync.set(K.strategy_pre_open(index), orjson.dumps(snapshot))
        return {"valid": False, "snapshot": snapshot, "missing": missing}

    redis_sync.set(K.strategy_pre_open(index), orjson.dumps(snapshot))
    log.info(f"pre_open_snapshot: captured {len(snapshot)} strikes for {index}")
    return {"valid": True, "snapshot": snapshot}
