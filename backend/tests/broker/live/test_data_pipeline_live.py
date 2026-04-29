"""
Live data-pipeline smoke test — gated by `pytest -m live` + UPSTOX_ACCESS_TOKEN.

Boots the engine in-process against the real Upstox WS feed for 30 s,
subscribed to the NIFTY ATM ±2 strikes, and asserts that the option_chain
JSON in Redis was updated at least once during the run.

Off-market hours: skips with reason; in-market: runs end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date

import orjson
import pytest

from brokers.upstox import UpstoxAPI
from engines.data_pipeline import (
    pre_market_subscriber,
    subscription_manager,
    tick_processor,
    ws_io,
)
from engines.data_pipeline.state import DataPipelineState
from state import keys as K
from state import redis_client

pytestmark = pytest.mark.live

TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
skip_no_token = pytest.mark.skipif(not TOKEN, reason="UPSTOX_ACCESS_TOKEN not set")


@skip_no_token
async def test_chain_updates_in_30_seconds() -> None:
    redis_client.init_pools()
    redis = redis_client.get_redis()

    # ── Build a minimal Redis seed: index_meta + option_chain + desired set
    cres = UpstoxAPI.get_option_contracts(
        {"instrument_key": "NSE_INDEX|Nifty 50", "access_token": TOKEN}
    )
    if not cres["success"]:
        pytest.skip(f"option_contracts failed: {cres['error']}")
    expiry = UpstoxAPI.nearest_expiry(cres["data"], today=date.today())
    if not expiry:
        pytest.skip("no future expiry returned")
    chain_res = UpstoxAPI.get_option_chain(
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "expiry_date": expiry,
            "access_token": TOKEN,
        }
    )
    if not chain_res["success"]:
        pytest.skip(f"option_chain failed: {chain_res['error']}")
    near = UpstoxAPI.strikes_around_atm(chain_res["data"], n_each_side=2)

    # Build the same chain template Init writes (Schema.md §1.3 shape).
    chain_template: dict[str, dict] = {}
    desired_tokens: set[str] = set()
    for r in near:
        strike = int(r["strike_price"])
        ce_key = (r.get("call_options") or {}).get("instrument_key")
        pe_key = (r.get("put_options") or {}).get("instrument_key")
        chain_template[str(strike)] = {
            "ce": (
                {
                    "token": ce_key,
                    "ltp": 0,
                    "bid": 0,
                    "ask": 0,
                    "bid_qty": 0,
                    "ask_qty": 0,
                    "vol": 0,
                    "oi": 0,
                    "ts": 0,
                }
                if ce_key
                else None
            ),
            "pe": (
                {
                    "token": pe_key,
                    "ltp": 0,
                    "bid": 0,
                    "ask": 0,
                    "bid_qty": 0,
                    "ask_qty": 0,
                    "vol": 0,
                    "oi": 0,
                    "ts": 0,
                }
                if pe_key
                else None
            ),
        }
        if ce_key:
            desired_tokens.add(ce_key)
        if pe_key:
            desired_tokens.add(pe_key)
    spot_token = "NSE_INDEX|Nifty 50"
    desired_tokens.add(spot_token)

    meta = {
        "strike_step": 50,
        "lot_size": 75,
        "exchange": "NFO",
        "spot_token": spot_token,
        "expiry": expiry,
        "atm_at_open": int(near[len(near) // 2]["strike_price"]),
    }

    pipe = redis.pipeline(transaction=False)
    pipe.set(K.market_data_index_meta("nifty50"), orjson.dumps(meta))
    pipe.set(K.market_data_index_option_chain("nifty50"), orjson.dumps(chain_template))
    pipe.set(K.market_data_index_meta("banknifty"), orjson.dumps({}))  # disabled
    pipe.set(K.market_data_index_option_chain("banknifty"), orjson.dumps({}))
    pipe.delete(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED)
    if desired_tokens:
        pipe.sadd(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED, *desired_tokens)
    await pipe.execute()

    # ── Build state + run loops for 30 s
    state = DataPipelineState(redis=redis, access_token=TOKEN, indexes=["nifty50"])
    state.index_meta["nifty50"] = meta
    state.chain["nifty50"] = chain_template
    state.token_index[spot_token] = ("nifty50", 0, "spot")
    for strike_str, sides in chain_template.items():
        strike = int(strike_str)
        for side in ("ce", "pe"):
            leaf = sides.get(side)
            if leaf and leaf.get("token"):
                state.token_index[leaf["token"]] = ("nifty50", strike, side)

    assert len(state.token_index) >= 5, f"only {len(state.token_index)} tokens indexed"

    chain_at_start = await redis.get(K.market_data_index_option_chain("nifty50"))

    async def _runner() -> None:
        await asyncio.gather(
            ws_io.ws_io_loop(state),
            tick_processor.tick_processor_loop(state),
            subscription_manager.subscription_manager_loop(state),
            pre_market_subscriber.subscribe_at_premarket(state),
        )

    runner_task = asyncio.create_task(_runner())
    try:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            if state.ticks_processed > 0 and state.last_flush_ts > 0:
                break
    finally:
        state.shutdown.set()
        try:
            await asyncio.wait_for(runner_task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            runner_task.cancel()

    chain_at_end = await redis.get(K.market_data_index_option_chain("nifty50"))

    if state.ticks_processed == 0:
        pytest.skip(
            f"no ticks received in 30s (likely off-market); "
            f"ws_connected={state.ws_connected.is_set()}"
        )
    assert state.ticks_processed > 0
    assert chain_at_end != chain_at_start, "option_chain JSON was not refreshed"

    parsed = orjson.loads(chain_at_end)
    has_live_leaf = any(
        (sides.get("ce") or {}).get("ltp", 0) > 0 or (sides.get("pe") or {}).get("ltp", 0) > 0
        for sides in parsed.values()
    )
    assert has_live_leaf, "no leaf has a non-zero ltp after run"
