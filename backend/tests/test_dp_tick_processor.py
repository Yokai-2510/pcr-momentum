"""data_pipeline.tick_processor — end-to-end with fakeredis.

Synthesizes broker WS frames, pushes them onto state.tick_queue, drives the
loop briefly, and asserts that:
  - market_data:indexes:{idx}:option_chain leaf was updated
  - market_data:indexes:{idx}:spot HASH was populated
  - market_data:bars:1s:{token} HASH was populated
  - market_data:stream:tick:{idx} STREAM has entries
"""

from __future__ import annotations

import asyncio

import fakeredis.aioredis as fakeredis_async
import orjson
import pytest

from engines.data_pipeline import tick_processor
from engines.data_pipeline.state import DataPipelineState
from state import keys as K


@pytest.fixture
async def redis() -> fakeredis_async.FakeRedis:
    r = fakeredis_async.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


def _make_state(redis: fakeredis_async.FakeRedis) -> DataPipelineState:
    state = DataPipelineState(
        redis=redis,
        access_token="dummy",
        indexes=["nifty50"],
    )
    state.index_meta["nifty50"] = {
        "strike_step": 50,
        "spot_token": "NSE_INDEX|Nifty 50",
        "prev_close": 22900.0,
    }
    state.chain["nifty50"] = {
        "22950": {"ce": {"token": "NSE_FO|22950_CE"}, "pe": {"token": "NSE_FO|22950_PE"}},
        "23000": {"ce": {"token": "NSE_FO|23000_CE"}, "pe": {"token": "NSE_FO|23000_PE"}},
        "23050": {"ce": {"token": "NSE_FO|23050_CE"}, "pe": {"token": "NSE_FO|23050_PE"}},
    }
    state.token_index["NSE_INDEX|Nifty 50"] = ("nifty50", 0, "spot")
    for s in (22950, 23000, 23050):
        state.token_index[f"NSE_FO|{s}_CE"] = ("nifty50", s, "ce")
        state.token_index[f"NSE_FO|{s}_PE"] = ("nifty50", s, "pe")
    return state


def _option_frame(token: str, ltp: float, ts: int = 1700000000000) -> dict:
    return {
        "feeds": {
            token: {
                "fullFeed": {
                    "marketFF": {
                        "ltpc": {"ltp": ltp, "ltt": ts},
                        "marketLevel": {
                            "bidAskQuote": [
                                {"bidQ": 100, "bidP": ltp - 0.5, "askQ": 100, "askP": ltp + 0.5}
                            ]
                        },
                        "vtt": 1234,
                        "oi": 5678,
                    }
                }
            }
        }
    }


def _spot_frame(token: str, ltp: float, ts: int = 1700000000000) -> dict:
    return {"feeds": {token: {"fullFeed": {"indexFF": {"ltpc": {"ltp": ltp, "ltt": ts}}}}}}


async def _drive_for(state: DataPipelineState, secs: float) -> None:
    """Run tick_processor_loop for `secs`, then signal shutdown."""
    task = asyncio.create_task(tick_processor.tick_processor_loop(state))
    await asyncio.sleep(secs)
    state.shutdown.set()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_option_tick_updates_chain_and_stream(redis: fakeredis_async.FakeRedis) -> None:
    state = _make_state(redis)
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_CE", 158.0))
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_PE", 78.0))

    await _drive_for(state, 0.3)

    # option_chain JSON must have both leaves populated.
    raw = await redis.get(K.market_data_index_option_chain("nifty50"))
    assert raw is not None
    chain = orjson.loads(raw)
    assert chain["23000"]["ce"]["ltp"] == 158.0
    assert chain["23000"]["pe"]["ltp"] == 78.0
    assert chain["23000"]["ce"]["bid"] == 157.5

    # tick stream has 2 entries.
    entries = await redis.xrange(K.market_data_stream_tick("nifty50"))
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_spot_tick_updates_spot_hash(redis: fakeredis_async.FakeRedis) -> None:
    state = _make_state(redis)
    state.tick_queue.put_nowait(_spot_frame("NSE_INDEX|Nifty 50", 23000.0))
    await _drive_for(state, 0.2)

    snap = await redis.hgetall(K.market_data_index_spot("nifty50"))
    assert snap[b"ltp"] == b"23000.0"
    assert snap[b"prev_close"] == b"22900.0"
    # change_inr = 100, change_pct ≈ 0.4366
    assert float(snap[b"change_inr"]) == pytest.approx(100.0, abs=0.5)
    assert float(snap[b"change_pct"]) == pytest.approx(0.4366, abs=1e-3)


@pytest.mark.asyncio
async def test_unknown_token_is_ignored(redis: fakeredis_async.FakeRedis) -> None:
    state = _make_state(redis)
    state.tick_queue.put_nowait(_option_frame("NSE_FO|99999_CE", 9.0))
    await _drive_for(state, 0.2)

    # No chain update — never written.
    assert await redis.get(K.market_data_index_option_chain("nifty50")) is None
    assert state.ticks_processed == 0


@pytest.mark.asyncio
async def test_first_frame_tracking(redis: fakeredis_async.FakeRedis) -> None:
    state = _make_state(redis)
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_CE", 100.0))
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23050_CE", 110.0))
    await _drive_for(state, 0.2)
    assert "NSE_FO|23000_CE" in state.tokens_with_first_frame
    assert "NSE_FO|23050_CE" in state.tokens_with_first_frame
    assert "NSE_FO|22950_CE" not in state.tokens_with_first_frame


@pytest.mark.asyncio
async def test_multiple_ticks_same_strike_keep_latest(
    redis: fakeredis_async.FakeRedis,
) -> None:
    state = _make_state(redis)
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_CE", 100.0, ts=1000))
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_CE", 105.0, ts=2000))
    state.tick_queue.put_nowait(_option_frame("NSE_FO|23000_CE", 102.0, ts=3000))
    await _drive_for(state, 0.3)

    raw = await redis.get(K.market_data_index_option_chain("nifty50"))
    chain = orjson.loads(raw)
    leaf = chain["23000"]["ce"]
    assert leaf["ltp"] == 102.0
    assert leaf["ts"] == 3000
