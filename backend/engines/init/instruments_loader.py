"""
engines.init.instruments_loader — bulk-load NSE master into Redis HASH.

Calls `UpstoxAPI.download_master_contract` to fetch the daily NSE.json.gz
from the Upstox CDN, then writes one entry per row into
`market_data:instruments:master` (HASH; field = instrument_key, value =
compact JSON of {symbol, expiry, strike, type, lot_size, exchange}).

The HASH is sized at ~100k entries; we write in batches of 500 to keep
Redis pipeline RTT reasonable.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from brokers.upstox import UpstoxAPI
from state import keys as K

# Default cache location (project-local, no root required). Can be overridden
# via INSTRUMENTS_CACHE_DIR env or kwarg to load_master_instruments.
_DEFAULT_CACHE_DIR = os.environ.get(
    "INSTRUMENTS_CACHE_DIR", "/home/ubuntu/premium_diff_bot/cache/instruments"
)


def _slimify(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the runtime engines need for token → metadata lookup.

    Reduces hash memory: master JSON has 20+ fields per row; we keep 8.
    """
    return {
        "symbol": row.get("trading_symbol") or row.get("tradingsymbol"),
        "expiry": row.get("expiry"),
        "strike": row.get("strike_price"),
        "type": row.get("instrument_type"),
        "lot_size": row.get("lot_size"),
        "tick_size": row.get("tick_size"),
        "exchange": row.get("exchange"),
        "segment": row.get("segment"),
    }


async def load_master_instruments(
    redis: _redis_async.Redis,
    cache_dir: str | Path | None = None,
) -> int:
    """Download the NSE master and write each row into `instruments:master`.

    Returns the number of rows written. On REST failure returns 0 and logs.
    """
    cache_path = Path(cache_dir or _DEFAULT_CACHE_DIR)
    res = UpstoxAPI.download_master_contract({"cache_dir": cache_path})
    if not res["success"]:
        logger.error(f"instruments_loader: download failed: {res['error']!r}")
        return 0

    json_path = Path(res["data"]["json_path"])
    with open(json_path, encoding="utf-8") as f:
        rows = json.load(f)

    # Wipe-then-rewrite so stale instruments don't linger across days.
    pipe = redis.pipeline(transaction=False)
    pipe.delete(K.MARKET_DATA_INSTRUMENTS_MASTER)
    await pipe.execute()

    written = 0
    BATCH = 500
    pipe = redis.pipeline(transaction=False)
    queued = 0
    for row in rows:
        key = row.get("instrument_key")
        if not key:
            continue
        pipe.hset(
            K.MARKET_DATA_INSTRUMENTS_MASTER,
            key,
            orjson.dumps(_slimify(row)).decode(),
        )
        queued += 1
        if queued >= BATCH:
            await pipe.execute()
            written += queued
            pipe = redis.pipeline(transaction=False)
            queued = 0
    if queued:
        await pipe.execute()
        written += queued

    await redis.set(K.MARKET_DATA_INSTRUMENTS_LAST_REFRESH_TS, str(int(time.time() * 1000)))
    logger.info(f"instruments_loader: loaded {written} instruments")
    return written
