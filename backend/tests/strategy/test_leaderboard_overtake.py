"""leaderboard_overtake_v1 — overtake detection + entry pipeline tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.init.redis_template import (
    DEFAULT_STRATEGY_CONFIG_OVERTAKE,
    UNIVERSE_INSTRUMENT_CONFIGS,
)
from engines.strategy.strategies.base import ActionKind, MarketView, VesselContext
from engines.strategy.strategies.leaderboard_overtake import (
    STRATEGY_DESCRIPTION,
    STRATEGY_ID,
    STRATEGY_NAME,
    LeaderboardOvertakeStrategy,
)
from engines.strategy.strategies.leaderboard_overtake.strategy import check_overtake_churn
from engines.strategy.strategies.nifty50_common import ranking

_IST = ZoneInfo("Asia/Kolkata")

SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]
STRIKES = [900, 950, 1000, 1050, 1100]


def ms_at(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(datetime(2026, 7, 6, h, m, s, tzinfo=_IST).timestamp() * 1000)


def _spot(symbol_pcts: dict[str, float], *, ts: int) -> dict[str, dict[str, Any]]:
    out = {}
    for sym, pct in symbol_pcts.items():
        prev = 1000.0
        out[sym] = {
            "ltp": prev * (1 + pct / 100),
            "prev_close": prev,
            "change_pct": pct,
            "volume": 1000,
            "ts": ts,
        }
    return out


def _chains(ltp: float, ts: int) -> dict[str, dict[str, Any]]:
    return {
        sym: {
            str(s): {
                "ce": {"token": f"{sym}CE{s}", "ltp": ltp, "ts": ts},
                "pe": {"token": f"{sym}PE{s}", "ltp": ltp, "ts": ts},
            }
            for s in STRIKES
        }
        for sym in SYMBOLS
    }


def _market(
    spot: dict[str, dict[str, Any]], chains: dict[str, dict[str, Any]], now_ms: int
) -> MarketView:
    return MarketView(
        chain={},
        spot=spot,
        meta={
            "symbols": {s: {"instrument_id": f"stk_{s.lower()}"} for s in SYMBOLS},
            "token_map": {f"{s}CE{k}": {} for s in SYMBOLS for k in STRIKES},
        },
        now_ms=now_ms,
        token_lookup=lambda _s, _o: None,
        read_chain=lambda sym: chains.get(sym, {}),
    )


def _ctx(**overrides: Any) -> VesselContext:
    cfg = {**DEFAULT_STRATEGY_CONFIG_OVERTAKE}
    # Basic direction mode keeps entry tests independent of premium bias.
    cfg["direction_prediction"] = {
        **cfg["direction_prediction"],
        "mode": "basic",
        "neutral_fallback": "category",
    }
    cfg["leaderboard"] = {**cfg["leaderboard"], "min_stocks_for_ranking": 2}
    cfg.update(overrides)
    return VesselContext(
        strategy_id=STRATEGY_ID,
        instrument_id="nifty50_stocks",
        strategy_config=cfg,
        instrument_config=dict(UNIVERSE_INSTRUMENT_CONFIGS[STRATEGY_ID]["nifty50_stocks"]),
    )


def _tick(strat: Any, ctx: Any, memory: Any, market: MarketView) -> Any:
    return strat.on_tick(ctx, strat.build_snapshot(ctx, memory, market), memory)


class TestMetadata:
    def test_name_and_description(self) -> None:
        assert STRATEGY_NAME == "Leaderboard Overtake"
        assert "overtake" in STRATEGY_DESCRIPTION.lower()
        assert DEFAULT_STRATEGY_CONFIG_OVERTAKE["name"] == STRATEGY_NAME


class TestOvertakeDetection:
    def test_detects_rank1_change_with_prior_visibility(self) -> None:
        prev = [
            {"symbol": "A", "rank": 1, "change_pct": 3.0, "ltp": 1},
            {"symbol": "B", "rank": 2, "change_pct": 2.0, "ltp": 1},
        ]
        curr = [
            {"symbol": "B", "rank": 1, "change_pct": 3.5, "ltp": 1},
            {"symbol": "A", "rank": 2, "change_pct": 3.0, "ltp": 1},
        ]
        event = ranking.detect_overtake(prev, curr, "GAINER", 123)
        assert event is not None
        assert event["symbol"] == "B" and event["old_rank"] == 2
        assert event["previous_rank_1"] == "A"

        # Same leader -> no event; unseen symbol -> no event.
        assert ranking.detect_overtake(curr, curr, "GAINER", 124) is None
        newcomer = [{"symbol": "Z", "rank": 1, "change_pct": 9.0, "ltp": 1}]
        assert ranking.detect_overtake(prev, newcomer, "GAINER", 125) is None


class TestChurnFilter:
    def _overtake(self, sym: str, prev1: str, ts_ms: int) -> dict[str, Any]:
        return {
            "symbol": sym,
            "previous_rank_1": prev1,
            "category": "GAINER",
            "timestamp_ms": ts_ms,
        }

    def test_pair_flip_rejected(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        memory = strat.create_memory(_ctx())
        cfg = {"enabled": True, "reject_pair_flip": True}
        ok, _ = check_overtake_churn(memory, self._overtake("B", "A", 1000), cfg)
        assert ok is True
        # A immediately takes back rank 1 from B -> ping-pong rejected.
        ok, reason = check_overtake_churn(memory, self._overtake("A", "B", 2000), cfg)
        assert ok is False and reason.startswith("OVERTAKE_PAIR_FLIP")

    def test_symbol_cooldown(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        memory = strat.create_memory(_ctx())
        cfg = {"enabled": True, "reject_pair_flip": False, "symbol_cooldown_seconds": 60}
        ok, _ = check_overtake_churn(memory, self._overtake("B", "A", 10_000), cfg)
        assert ok is True
        ok, reason = check_overtake_churn(memory, self._overtake("B", "C", 40_000), cfg)
        assert ok is False and reason.startswith("OVERTAKE_SYMBOL_COOLDOWN")


class TestEntryPipeline:
    def test_overtake_fires_entry(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)

        t1 = ms_at("09:20:00")
        chains = _chains(50.0, t1)
        # Establish baseline rankings: RELIANCE leads gainers.
        spot1 = _spot({"RELIANCE": 3.0, "TCS": 1.0, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t1)
        action = _tick(strat, ctx, memory, _market(spot1, chains, t1))
        assert action.kind == ActionKind.NO_OP  # no overtake on first sight

        # TCS overtakes RELIANCE for rank-1 gainer.
        t2 = ms_at("09:20:05")
        chains2 = _chains(50.0, t2)
        spot2 = _spot({"RELIANCE": 3.0, "TCS": 3.6, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t2)
        action = _tick(strat, ctx, memory, _market(spot2, chains2, t2))
        assert action.kind == ActionKind.ENTER
        assert action.side == "CE"
        assert action.instrument_token.startswith("TCSCE")
        assert action.snapshot["overtake"]["symbol"] == "TCS"
        assert action.snapshot["overtake"]["previous_rank_1"] == "RELIANCE"
        assert memory.entries_today == 1
        # Analytics: leaderboard top-5 present in metrics.
        assert action.metrics["leaderboard"]["top_gainers"][0]["symbol"] == "TCS"

    def test_reentry_cooldown_blocks_pyramiding(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        t = ms_at("09:25:00")
        memory.entered_symbols["TCS"] = t - 60_000  # entered 60s ago (cooldown 900s)
        memory.prev_gainers, memory.prev_losers = ranking.rank(
            ranking.valid_stocks(
                _spot({"RELIANCE": 3.0, "TCS": 1.0, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
            )
        )
        spot = _spot({"RELIANCE": 3.0, "TCS": 3.6, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
        action = _tick(strat, ctx, memory, _market(spot, _chains(50.0, t), t))
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("SYMBOL_REENTRY_COOLDOWN")

    def test_entry_freeze_after_cutoff(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        t = ms_at("15:05:00")
        memory.prev_gainers, memory.prev_losers = ranking.rank(
            ranking.valid_stocks(
                _spot({"RELIANCE": 3.0, "TCS": 1.0, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
            )
        )
        spot = _spot({"RELIANCE": 3.0, "TCS": 3.6, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
        action = _tick(strat, ctx, memory, _market(spot, _chains(50.0, t), t))
        assert action.kind == ActionKind.NO_OP
        assert action.reason == "entry_freeze_window"
        assert not memory.pending_overtakes

    def test_stale_option_leaf_blocks(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        t = ms_at("09:25:00")
        memory.prev_gainers, memory.prev_losers = ranking.rank(
            ranking.valid_stocks(
                _spot({"RELIANCE": 3.0, "TCS": 1.0, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
            )
        )
        spot = _spot({"RELIANCE": 3.0, "TCS": 3.6, "HDFCBANK": 0.0, "INFY": -1.0}, ts=t)
        stale_chains = _chains(50.0, t - 60_000)  # leaves 60s old (max 10s)
        action = _tick(strat, ctx, memory, _market(spot, stale_chains, t))
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("OPTION_TICK_STALE")

    def test_premarket_snapshot_capture(self) -> None:
        strat = LeaderboardOvertakeStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        t = ms_at("09:12:00")
        spot = _spot({"RELIANCE": 1.2, "TCS": -0.8, "HDFCBANK": 0.1, "INFY": 0.0}, ts=t)
        action = _tick(strat, ctx, memory, _market(spot, _chains(50.0, t), t))
        assert action.reason == "settlement_snapshots_captured"
        assert memory.premarket_leaderboard == {"top_gainer_pct": 1.2, "top_loser_pct": -0.8}
        assert memory.premium_snapshots is not None
