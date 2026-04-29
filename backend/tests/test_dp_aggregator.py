"""data_pipeline.aggregator — pure helper tests."""

from __future__ import annotations

import pytest

from engines.data_pipeline.aggregator import (
    update_option_chain_leaf,
    update_spot_snapshot,
)
from engines.data_pipeline.parser import ParsedTick


def _tick(**kw: object) -> ParsedTick:
    base = {
        "token": "NSE_FO|X",
        "ltp": 100.0,
        "bid": 99.5,
        "ask": 100.5,
        "bid_qty": 100,
        "ask_qty": 200,
        "vol": 10_000,
        "oi": 5_000,
        "ts": 1700000000000,
    }
    base.update(kw)
    return ParsedTick(**base)  # type: ignore[arg-type]


def _empty_chain(strikes: list[int]) -> dict[str, dict[str, dict[str, object] | None]]:
    return {
        str(s): {
            "ce": {"token": f"NSE_FO|{s}_CE"},
            "pe": {"token": f"NSE_FO|{s}_PE"},
        }
        for s in strikes
    }


# ── update_option_chain_leaf ─────────────────────────────────────────────


def test_leaf_update_writes_all_fields() -> None:
    chain = _empty_chain([22900, 23000])
    tick = _tick(token="NSE_FO|23000_CE", ltp=158.0)
    update_option_chain_leaf(chain, 23000, "ce", tick)
    leaf = chain["23000"]["ce"]
    assert leaf is not None
    assert leaf["ltp"] == 158.0
    assert leaf["bid"] == 99.5
    assert leaf["ask"] == 100.5
    assert leaf["vol"] == 10_000
    assert leaf["oi"] == 5_000
    assert leaf["ts"] == 1700000000000
    # Token preserved from existing template (Init seeded it).
    assert leaf["token"] == "NSE_FO|23000_CE"


def test_leaf_update_preserves_token_when_tick_token_differs() -> None:
    chain = _empty_chain([23000])
    chain["23000"]["ce"] = {"token": "NSE_FO|original"}
    tick = _tick(token="NSE_FO|wrong")
    update_option_chain_leaf(chain, 23000, "ce", tick)
    assert chain["23000"]["ce"]["token"] == "NSE_FO|original"  # type: ignore[index]


def test_leaf_update_ignores_unknown_strike() -> None:
    chain = _empty_chain([23000])
    tick = _tick()
    update_option_chain_leaf(chain, 23050, "ce", tick)  # not in chain
    assert "23050" not in chain


def test_leaf_update_ignores_invalid_side() -> None:
    chain = _empty_chain([23000])
    tick = _tick()
    update_option_chain_leaf(chain, 23000, "xx", tick)
    # Only the seed token should remain.
    assert chain["23000"]["ce"] == {"token": "NSE_FO|23000_CE"}
    assert chain["23000"]["pe"] == {"token": "NSE_FO|23000_PE"}


def test_leaf_update_partial_tick_fills_with_existing() -> None:
    chain = _empty_chain([23000])
    chain["23000"]["ce"] = {
        "token": "NSE_FO|23000_CE",
        "ltp": 100.0,
        "bid": 99,
        "ask": 101,
        "vol": 5_000,
        "oi": 1_000,
    }
    tick = _tick(token="NSE_FO|23000_CE", ltp=110.0, bid=None, ask=None, vol=None, oi=None)
    update_option_chain_leaf(chain, 23000, "ce", tick)
    leaf = chain["23000"]["ce"]
    assert leaf is not None
    assert leaf["ltp"] == 110.0
    assert leaf["bid"] == 99  # preserved
    assert leaf["ask"] == 101
    assert leaf["vol"] == 5_000
    assert leaf["oi"] == 1_000


# ── update_spot_snapshot ─────────────────────────────────────────────────


def test_spot_snapshot_with_prev_close() -> None:
    snap = update_spot_snapshot(None, _tick(ltp=23000.0), prev_close=22900.0)
    assert snap["ltp"] == 23000.0
    assert snap["prev_close"] == 22900.0
    assert snap["change_inr"] == 100.0
    assert snap["change_pct"] == pytest.approx(0.4366, abs=1e-3)
    assert snap["ts"] == 1700000000000


def test_spot_snapshot_zero_prev_close_is_safe() -> None:
    snap = update_spot_snapshot(None, _tick(ltp=23000.0), prev_close=0)
    assert snap["change_inr"] == 0
    assert snap["change_pct"] == 0


def test_spot_snapshot_uses_existing_prev_close() -> None:
    snap = update_spot_snapshot({"prev_close": 22900.0}, _tick(ltp=23000.0))
    assert snap["change_inr"] == 100.0
