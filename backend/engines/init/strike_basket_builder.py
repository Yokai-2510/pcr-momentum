"""
engines.init.strike_basket_builder — Sequential_Flow §7 step 11.

Per index, builds the day's option-chain template + locked trading basket and
writes them to Redis. Pure helpers (compute_atm, window, template builders)
are extracted so they can be unit-tested without any I/O.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from state import keys as K

# Spot index identifier for each named index (Schema.md §1.3 + TDD §3.7)
INDEX_SPOT_TOKENS: dict[str, str] = {
    "nifty50": "NSE_INDEX|Nifty 50",
    "banknifty": "NSE_INDEX|Nifty Bank",
}


# ── Pure helpers ───────────────────────────────────────────────────────


def compute_atm(spot: float, strike_step: int) -> int:
    """Round-to-nearest ATM."""
    if strike_step <= 0:
        raise ValueError(f"strike_step must be > 0; got {strike_step}")
    return int(round(spot / strike_step) * strike_step)


def discover_nearest_expiry(contracts: list[dict[str, Any]], today: date) -> str | None:
    """First expiry >= today (ISO YYYY-MM-DD)."""
    today_iso = today.isoformat()
    expiries: list[str] = sorted({str(c["expiry"]) for c in contracts if c.get("expiry")})
    for exp in expiries:
        if exp >= today_iso:
            return exp
    return None


def filter_atm_window_strikes(
    contracts: list[dict[str, Any]],
    expiry: str,
    atm: int,
    step: int,
    window: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Return (strike_list, contracts_in_window) for ATM ± window strikes."""
    strikes = [atm + i * step for i in range(-window, window + 1)]
    strike_set = set(strikes)
    filtered = [
        c
        for c in contracts
        if c.get("expiry") == expiry and (c.get("strike_price") or 0) in strike_set
    ]
    return strikes, filtered


def build_option_chain_template(
    contracts_in_window: list[dict[str, Any]],
    strikes: list[int],
) -> dict[str, dict[str, Any]]:
    """Per-strike CE/PE skeleton with empty WS placeholders.

    Schema.md §1.3 option_chain shape.
    """
    by_strike: dict[int, dict[str, dict[str, Any] | None]] = {
        s: {"ce": None, "pe": None} for s in strikes
    }
    for c in contracts_in_window:
        strike = int(c.get("strike_price") or 0)
        side = (c.get("instrument_type") or "").upper()
        if strike not in by_strike or side not in {"CE", "PE"}:
            continue
        by_strike[strike][side.lower()] = {
            "token": c.get("instrument_key"),
            "ltp": 0,
            "bid": 0,
            "ask": 0,
            "bid_qty": 0,
            "ask_qty": 0,
            "vol": 0,
            "oi": 0,
            "ts": 0,
        }
    # Convert keys to strings for orjson stability
    return {str(s): dict(by_strike[s]) for s in strikes}


def build_trading_basket(
    option_chain: dict[str, dict[str, Any]],
    atm: int,
    step: int,
    range_n: int,
) -> dict[str, list[str]]:
    """ATM ∓ range_n CE tokens (ITM calls) + ATM ± range_n PE tokens (ITM puts).

    Per Strategy.md §4.1:
      CE basket = ATM, ATM-step, ATM-2*step  (ITM CE — i.e. lower strikes)
      PE basket = ATM, ATM+step, ATM+2*step  (ITM PE — i.e. higher strikes)
    """
    ce_tokens: list[str] = []
    pe_tokens: list[str] = []
    for i in range(range_n + 1):
        ce_strike = str(atm - i * step)
        pe_strike = str(atm + i * step)
        ce_leaf = (option_chain.get(ce_strike) or {}).get("ce")
        pe_leaf = (option_chain.get(pe_strike) or {}).get("pe")
        if ce_leaf and ce_leaf.get("token"):
            ce_tokens.append(ce_leaf["token"])
        if pe_leaf and pe_leaf.get("token"):
            pe_tokens.append(pe_leaf["token"])
    return {"ce": ce_tokens, "pe": pe_tokens}


# ── I/O orchestrator ──────────────────────────────────────────────────


async def _read_index_config(redis: _redis_async.Redis, index: str) -> dict[str, Any]:
    raw = await redis.get(K.strategy_config_index(index))
    if not raw:
        raise RuntimeError(f"strike_basket_builder: missing config for {index}")
    if isinstance(raw, str):
        raw = raw.encode()
    parsed: dict[str, Any] = orjson.loads(raw)
    return parsed


async def build_for_index(
    redis: _redis_async.Redis,
    index: str,
    access_token: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Compute ATM + nearest expiry + basket for one index, persist to Redis.

    Returns: {atm, expiry, tokens, ce_basket, pe_basket}.

    On any failure: SET strategy:{index}:enabled = "false" and return
    {"error": "..."} (non-fatal — caller continues with the other index).
    """
    cfg = await _read_index_config(redis, index)
    spot_token = INDEX_SPOT_TOKENS[index]
    today = today or date.today()

    # 1. Spot LTP
    ltp_res = UpstoxAPI.get_ltp({"instrument_keys": [spot_token], "access_token": access_token})
    if not ltp_res["success"] or spot_token not in (ltp_res["data"] or {}):
        await redis.set(K.strategy_enabled(index), "false")
        return {"error": f"spot_ltp_failed: {ltp_res['error']}"}
    spot = float(ltp_res["data"][spot_token])

    # 2. ATM
    step = int(cfg["strike_step"])
    atm = compute_atm(spot, step)

    # 3. All option contracts for this underlying
    cres = UpstoxAPI.get_option_contracts(
        {"instrument_key": spot_token, "access_token": access_token}
    )
    if not cres["success"]:
        await redis.set(K.strategy_enabled(index), "false")
        return {"error": f"contracts_failed: {cres['error']}"}
    contracts = cres["data"] or []

    # 4. Nearest expiry
    expiry = discover_nearest_expiry(contracts, today)
    if not expiry:
        await redis.set(K.strategy_enabled(index), "false")
        return {"error": "no_future_expiry"}

    # 5. Filter to ATM ± subscription window
    window = int(cfg["pre_open_subscribe_window"])
    strikes, in_window = filter_atm_window_strikes(contracts, expiry, atm, step, window)

    # 6. Chain template + 7. trading basket
    chain = build_option_chain_template(in_window, strikes)
    basket = build_trading_basket(chain, atm, step, int(cfg["trading_basket_range"]))

    # Determine lot_size: use first contract's lot_size, else config fallback.
    lot_size = int(cfg["lot_size"])
    for c in in_window:
        if c.get("lot_size"):
            lot_size = int(c["lot_size"])
            break

    meta = {
        "strike_step": step,
        "lot_size": lot_size,
        "exchange": cfg.get("exchange", "NFO"),
        "spot_token": spot_token,
        "expiry": expiry,
        "prev_close": None,  # Phase 5 (data pipeline) populates from full quote
        "atm_at_open": atm,
        "ce_strikes": strikes,
        "pe_strikes": strikes,
    }

    # 8. Persist
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.market_data_index_meta(index), orjson.dumps(meta))
    pipe.set(K.market_data_index_option_chain(index), orjson.dumps(chain))
    pipe.set(K.strategy_basket(index), orjson.dumps(basket))

    # 9. Add tokens to subscription:desired
    all_tokens = {c["instrument_key"] for c in in_window if c.get("instrument_key")}
    all_tokens.add(spot_token)
    if all_tokens:
        pipe.sadd(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED, *all_tokens)
    await pipe.execute()

    logger.info(
        f"strike_basket_builder[{index}]: spot={spot:.2f} atm={atm} expiry={expiry} "
        f"chain={len(chain)} basket=(ce={len(basket['ce'])}, pe={len(basket['pe'])}) "
        f"tokens={len(all_tokens)}"
    )
    return {
        "atm": atm,
        "expiry": expiry,
        "tokens": len(all_tokens),
        "ce_basket": basket["ce"],
        "pe_basket": basket["pe"],
        "spot": spot,
    }
