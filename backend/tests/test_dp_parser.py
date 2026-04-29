"""data_pipeline.parser — frame decoding."""

from __future__ import annotations

from engines.data_pipeline.parser import parse_tick


def test_empty_frame_returns_empty_list() -> None:
    assert parse_tick({}) == []
    assert parse_tick({"feeds": {}}) == []
    assert parse_tick({"feeds": "not-a-dict"}) == []  # type: ignore[arg-type]


def test_non_dict_frame_returns_empty_list() -> None:
    assert parse_tick(None) == []  # type: ignore[arg-type]
    assert parse_tick("string") == []  # type: ignore[arg-type]


def test_v3_full_feed_market_ff_shape() -> None:
    """Upstox v3 'full' mode for an option leg."""
    frame = {
        "type": "live_feed",
        "feeds": {
            "NSE_FO|49520": {
                "fullFeed": {
                    "marketFF": {
                        "ltpc": {"ltp": 158.0, "ltt": 1714290330123, "ltq": 75, "cp": 156.0},
                        "marketLevel": {
                            "bidAskQuote": [
                                {"bidQ": 1500, "bidP": 157.5, "askQ": 1200, "askP": 158.5}
                            ]
                        },
                        "vtt": 234500,
                        "oi": 67800,
                    }
                }
            }
        },
    }
    ticks = parse_tick(frame)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.token == "NSE_FO|49520"
    assert t.ltp == 158.0
    assert t.bid == 157.5
    assert t.ask == 158.5
    assert t.bid_qty == 1500
    assert t.ask_qty == 1200
    assert t.vol == 234500
    assert t.oi == 67800
    assert t.ts == 1714290330123


def test_index_feed_shape() -> None:
    """Index/spot ticks have indexFF instead of marketFF."""
    frame = {
        "feeds": {
            "NSE_INDEX|Nifty 50": {
                "fullFeed": {
                    "indexFF": {"ltpc": {"ltp": 22950.5, "ltt": 1714290330123, "cp": 22900.0}}
                }
            }
        }
    }
    ticks = parse_tick(frame)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.token == "NSE_INDEX|Nifty 50"
    assert t.ltp == 22950.5
    assert t.bid is None
    assert t.ask is None


def test_flat_token_keyed_frame() -> None:
    """Some shapes drop the 'feeds' wrapper."""
    frame = {"NSE_FO|49521": {"ltpc": {"ltp": 78.0, "ltt": 1714290330999}}}
    ticks = parse_tick(frame)
    assert len(ticks) == 1
    assert ticks[0].token == "NSE_FO|49521"
    assert ticks[0].ltp == 78.0
    assert ticks[0].ts == 1714290330999


def test_multi_feed_frame() -> None:
    frame = {
        "feeds": {
            "NSE_FO|A": {"ltpc": {"ltp": 1.0, "ltt": 1000}},
            "NSE_FO|B": {"ltpc": {"ltp": 2.0, "ltt": 2000}},
            "NSE_FO|C": {"ltpc": {"ltp": 3.0, "ltt": 3000}},
        }
    }
    ticks = parse_tick(frame)
    assert {t.token for t in ticks} == {"NSE_FO|A", "NSE_FO|B", "NSE_FO|C"}
    assert {t.ltp for t in ticks} == {1.0, 2.0, 3.0}


def test_missing_ltp_yields_none() -> None:
    frame = {"feeds": {"NSE_FO|X": {"fullFeed": {"marketFF": {"oi": 100}}}}}
    ticks = parse_tick(frame)
    assert len(ticks) == 1
    assert ticks[0].ltp is None
    assert ticks[0].oi == 100


def test_garbage_values_become_none() -> None:
    frame = {
        "feeds": {
            "NSE_FO|Y": {
                "ltpc": {"ltp": "not-a-number", "ltt": 1000},
                "fullFeed": {"marketFF": {"oi": ""}},
            }
        }
    }
    ticks = parse_tick(frame)
    assert ticks[0].ltp is None
    assert ticks[0].oi is None


def test_default_ts_when_no_ltt() -> None:
    frame = {"feeds": {"NSE_FO|Z": {"ltpc": {"ltp": 5.0}}}}
    ticks = parse_tick(frame)
    assert ticks[0].ts > 0
