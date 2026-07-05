"""
engines.init.nifty50_universe — NIFTY-50 stock + stock-options universe build.

Strategies can demand data that does NOT arrive on the websocket (index
constituents, previous-close references, instrument maps). This module is
the first such external-data provider; `UNIVERSE_BUILDERS` at the bottom is
the extension point init consults for every registered universe instrument.

Constituents fetch is a VERBATIM port of the original rank-momentum
`nifty_50_fetcher` — same URL, same headers, same CSV column parsing, same
cache-until-07:00-IST staleness rule, same cached-fallback-on-error. Do not
change the URL or headers; NSE/niftyindices only serves this combination.

Universe build (port of the original `instruments_manager`):
    NSE_EQ rows for the 50 symbols        -> spot tokens + prev_close hint
    NSE_FO CE/PE rows, nearest expiry,    -> per-symbol option chains,
    ATM ± atm_strike_range strikes           ATM from last_price, strike
                                             step derived from actual gaps

Redis writes (single-writer: init, at boot):
    market_data:universes                       SADD {universe_id}
    market_data:{universe_id}:meta              JSON {symbols, token_map}
    market_data:{universe_id}:spot              HASH symbol -> JSON snapshot
    market_data:{stk_instrument_id}:option_chain   chain skeletons
    market_data:subscriptions:desired           SADD all tokens (full set —
                                                all 50 stocks + all strikes)
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import redis.asyncio as _redis_async
import requests
from loguru import logger

from state import keys as K

_IST = ZoneInfo("Asia/Kolkata")
_DAILY_REFRESH_HOUR = 7  # Re-fetch if cache is from before 07:00 IST today

UNIVERSE_ID = "nifty50_stocks"

# ── Constituents fetch (verbatim configuration from the original) ──────────
CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
ACCEPT = "text/csv,*/*;q=0.8"
CACHE_FILENAME = "ind_nifty50list.csv"
FETCH_TIMEOUT_SEC = 10
EXPECTED_SYMBOLS = 50

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "pcr-momentum"

ATM_STRIKE_RANGE = 10  # ATM ± N strikes per symbol (original atm_strike_range)


def symbol_instrument_id(symbol: str) -> str:
    """Normalize an NSE symbol to a platform instrument id: 'M&M' -> 'stk_m_m'."""
    return "stk_" + re.sub(r"[^a-z0-9]+", "_", symbol.lower()).strip("_")


def _cache_is_stale(csv_path: Path) -> bool:
    """True if the cache file is from before today's 07:00 IST (original rule)."""
    if not csv_path.exists():
        return True
    mtime = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=_IST)
    now = datetime.now(_IST)
    today_refresh = now.replace(hour=_DAILY_REFRESH_HOUR, minute=0, second=0, microsecond=0)
    return mtime.date() < now.date() or (mtime.date() == now.date() and mtime < today_refresh)


def parse_constituents_csv(content: bytes) -> list[str]:
    """Symbols from column index 2, skipping the header, DUMMY rows filtered."""
    symbols: list[str] = []
    reader = csv.reader(io.StringIO(content.decode("utf-8", errors="replace")))
    next(reader, None)  # skip header
    for row in reader:
        if len(row) >= 3:
            symbol = row[2].strip().strip('"')
            if symbol and "DUMMY" not in symbol.upper():
                symbols.append(symbol)
    return symbols


def fetch_nifty50_constituents(cache_dir: str | Path | None = None) -> list[str]:
    """Download (or reuse cached) NIFTY-50 constituents list. Returns symbols.

    Behavior is the original's: cache until 07:00 IST, download with the
    exact URL/headers, fall back to a stale cache if the network fails.
    """
    log = logger.bind(engine="init", component="nifty50_universe")
    cache = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
    csv_path = cache / CACHE_FILENAME

    if not _cache_is_stale(csv_path):
        symbols = parse_constituents_csv(csv_path.read_bytes())
        if symbols:
            log.info(f"nifty50 constituents: {len(symbols)} symbols (cached)")
            return symbols

    try:
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
        response = requests.get(CSV_URL, headers=headers, timeout=FETCH_TIMEOUT_SEC)
        response.raise_for_status()
        cache.mkdir(parents=True, exist_ok=True)
        csv_path.write_bytes(response.content)
        symbols = parse_constituents_csv(response.content)
        note = "" if len(symbols) == EXPECTED_SYMBOLS else f", expected {EXPECTED_SYMBOLS}"
        log.info(f"nifty50 constituents: {len(symbols)} symbols ready (fresh{note})")
        return symbols
    except Exception as e:
        log.error(f"nifty50 constituents fetch failed: {e!r}")
        if csv_path.exists():
            log.warning("using cached constituents after fetch failure")
            return parse_constituents_csv(csv_path.read_bytes())
        return []


# ── Universe build from the broker instruments master ──────────────────────


def _empty_leaf(token: str) -> dict[str, Any]:
    """Chain-skeleton leaf matching the platform leaf shape (Schema.md §1.3)."""
    return {
        "token": token,
        "ltp": 0,
        "bid": 0,
        "ask": 0,
        "bid_qty": 0,
        "ask_qty": 0,
        "vol": 0,
        "oi": 0,
        "ts": 0,
    }


def build_universe_maps(
    master_rows: list[dict[str, Any]],
    symbols: list[str],
    *,
    atm_strike_range: int = ATM_STRIKE_RANGE,
) -> dict[str, Any]:
    """Pure build: master rows + symbols -> {symbols, token_map, chains}.

    Port of the original `_map_stocks` + `_map_options` (plain dicts, no
    pandas): nearest expiry per symbol, ATM from last_price, strike step
    derived from the smallest gap between adjacent strikes, ATM ± range.
    """
    eq_by_symbol: dict[str, dict[str, Any]] = {}
    fo_by_symbol: dict[str, list[dict[str, Any]]] = {}
    want = set(symbols)

    for row in master_rows:
        seg = row.get("segment")
        if seg == "NSE_EQ":
            sym = row.get("trading_symbol") or row.get("tradingsymbol")
            if sym in want:
                eq_by_symbol[str(sym)] = row
        elif seg == "NSE_FO" and row.get("instrument_type") in ("CE", "PE"):
            underlying = row.get("underlying_symbol") or row.get("asset_symbol")
            if underlying in want:
                fo_by_symbol.setdefault(str(underlying), []).append(row)

    now_ms = int(datetime.now(_IST).timestamp() * 1000)
    universe_symbols: dict[str, Any] = {}
    token_map: dict[str, Any] = {}
    chains: dict[str, dict[str, Any]] = {}  # instrument_id -> chain skeleton

    for symbol in symbols:
        eq = eq_by_symbol.get(symbol)
        if eq is None:
            continue
        sidx = symbol_instrument_id(symbol)
        spot_token = str(eq.get("instrument_key") or "")
        prev_close = float(eq.get("last_price") or 0.0)

        contracts = [
            r for r in fo_by_symbol.get(symbol, []) if (r.get("expiry") or 0) >= now_ms - 86400_000
        ]
        chain: dict[str, Any] = {}
        lot_size = int(eq.get("lot_size") or 1)
        expiry_ms = 0
        if contracts:
            expiry_ms = min(int(r.get("expiry") or 0) for r in contracts)
            exp_rows = [r for r in contracts if int(r.get("expiry") or 0) == expiry_ms]
            lot_size = int(exp_rows[0].get("lot_size") or 1) if exp_rows else lot_size

            strikes = sorted({int(float(r.get("strike_price") or 0)) for r in exp_rows})
            if strikes:
                spot_hint = prev_close if prev_close > 0 else strikes[len(strikes) // 2]
                atm = min(strikes, key=lambda x: abs(x - spot_hint))
                if len(strikes) >= 2:
                    diffs = [strikes[i + 1] - strikes[i] for i in range(min(len(strikes) - 1, 10))]
                    step = min(diffs) if diffs else 50
                else:
                    step = 50
                lo = atm - atm_strike_range * step
                hi = atm + atm_strike_range * step
                for r in exp_rows:
                    strike = int(float(r.get("strike_price") or 0))
                    if strike < lo or strike > hi:
                        continue
                    side = str(r.get("instrument_type")).lower()  # "ce" | "pe"
                    token = str(r.get("instrument_key") or "")
                    if not token:
                        continue
                    slot = chain.setdefault(str(strike), {"ce": None, "pe": None})
                    slot[side] = _empty_leaf(token)
                    token_map[token] = {
                        "symbol": symbol,
                        "instrument_id": sidx,
                        "strike": strike,
                        "side": side.upper(),
                        "lot_size": int(r.get("lot_size") or lot_size),
                    }

        universe_symbols[symbol] = {
            "instrument_id": sidx,
            "spot_token": spot_token,
            "lot_size": lot_size,
            "prev_close": prev_close,
            "expiry_ms": expiry_ms,
            "strikes": sorted(int(s) for s in chain),
        }
        token_map[spot_token] = {
            "symbol": symbol,
            "instrument_id": sidx,
            "strike": 0,
            "side": "SPOT",
            "lot_size": lot_size,
        }
        chains[sidx] = chain

    return {"symbols": universe_symbols, "token_map": token_map, "chains": chains}


async def build_nifty50_universe(
    redis: _redis_async.Redis,
    master_rows: list[dict[str, Any]],
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, int]:
    """Fetch constituents, build maps, seed all universe keys + subscriptions."""
    log = logger.bind(engine="init", component="nifty50_universe")
    symbols = fetch_nifty50_constituents(cache_dir)
    if not symbols:
        return {"error": 1, "symbols": 0, "tokens": 0}

    maps = build_universe_maps(master_rows, symbols)
    universe_symbols = maps["symbols"]
    token_map = maps["token_map"]
    chains = maps["chains"]

    meta = {
        "universe": UNIVERSE_ID,
        "built_ts": int(datetime.now(_IST).timestamp() * 1000),
        "symbols": universe_symbols,
        "token_map": token_map,
    }

    pipe = redis.pipeline(transaction=False)
    pipe.sadd(K.MARKET_DATA_UNIVERSES, UNIVERSE_ID)
    pipe.set(K.market_data_index_meta(UNIVERSE_ID), orjson.dumps(meta))
    # Aggregate spot hash seeded so ranking strategies see all symbols at boot.
    for symbol, info in universe_symbols.items():
        pipe.hset(
            K.market_data_index_spot(UNIVERSE_ID),
            symbol,
            orjson.dumps(
                {
                    "ltp": 0,
                    "prev_close": info["prev_close"],
                    "change_pct": 0,
                    "volume": 0,
                    "ts": 0,
                }
            ).decode(),
        )
    # Per-symbol chain skeletons (data_pipeline builds its token index from these).
    for sidx, chain in chains.items():
        pipe.set(K.market_data_index_option_chain(sidx), orjson.dumps(chain))
    # COMPLETE subscription: every stock + every mapped option strike.
    all_tokens = list(token_map.keys())
    if all_tokens:
        pipe.sadd(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED, *all_tokens)
    await pipe.execute()

    n_opt = sum(1 for v in token_map.values() if v["side"] != "SPOT")
    log.info(
        f"universe {UNIVERSE_ID}: {len(universe_symbols)} symbols, "
        f"{n_opt} option tokens, {len(all_tokens)} total subscriptions"
    )
    return {"symbols": len(universe_symbols), "tokens": len(all_tokens), "options": n_opt}


# ── Extension point: universes init knows how to build ─────────────────────
# Init consults this for every registered vessel whose instrument_id matches;
# a new universe (or a strategy demanding other external data) adds an entry.
UNIVERSE_BUILDERS = {UNIVERSE_ID: build_nifty50_universe}
