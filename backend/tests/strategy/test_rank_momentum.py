"""rank_momentum_v2 — Foolproof spec formulas + entry/exit engines."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.init.redis_template import (
    DEFAULT_STRATEGY_CONFIG_RANK_MOMENTUM,
    _pcr_instrument_config,
)
from engines.strategy.strategies.base import ActionKind, MarketView, VesselContext
from engines.strategy.strategies.rank_momentum import RankMomentumStrategy, flow

_IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = [f"SYM{i}" for i in range(12)]
STRIKES = [900, 950, 1000, 1050, 1100]


def ms_at(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(datetime(2026, 7, 6, h, m, s, tzinfo=_IST).timestamp() * 1000)


def _chain(sym: str, *, ce_vol: float, pe_vol: float, ltp: float, ts: int) -> dict[str, Any]:
    return {
        str(s): {
            "ce": {"token": f"{sym}CE{s}", "ltp": ltp, "vol": ce_vol, "ts": ts},
            "pe": {"token": f"{sym}PE{s}", "ltp": ltp, "vol": pe_vol, "ts": ts},
        }
        for s in STRIKES
    }


def _market(chains: dict[str, Any], spots: dict[str, float], now_ms: int) -> MarketView:
    return MarketView(
        chain={},
        spot={
            s: {"ltp": v, "prev_close": 1000.0, "volume": 100, "ts": now_ms}
            for s, v in spots.items()
        },
        meta={
            "symbols": {s: {"instrument_id": f"stk_{s.lower()}"} for s in SYMBOLS},
            "token_map": {f"{s}CE{k}": {} for s in SYMBOLS for k in STRIKES},
        },
        now_ms=now_ms,
        token_lookup=lambda _s, _o: None,
        read_chain=lambda sym: chains.get(sym, {}),
    )


def _ctx(**overrides: Any) -> VesselContext:
    cfg = {
        k: (dict(v) if isinstance(v, dict) else v)
        for k, v in DEFAULT_STRATEGY_CONFIG_RANK_MOMENTUM.items()
    }
    cfg["indicator"].update(
        {
            "min_symbols": 5,
            "compute_interval_ms": 0,
            "nd_threshold": 100_000,
            "confidence_min": 0.30,
        }
    )
    cfg["indicator"].update(overrides.pop("indicator", {}))
    cfg.update(overrides)
    return VesselContext(
        strategy_id="rank_momentum_v2",
        instrument_id="nifty50_stocks",
        strategy_config=cfg,
        instrument_config=_pcr_instrument_config("nifty50"),
    )


class TestFlowFormulas:
    def test_net_delta_and_velocity(self) -> None:
        f = flow.SymbolFlow()
        t0 = ms_at("09:15:00")
        f.update(t0, 1_000_000, 1_000_000, 1000.0)
        assert f.nd == 0.0  # session baseline
        f.update(t0 + 30_000, 2_000_000, 1_200_000, 1001.0)
        # ND = CEΔ − PEΔ = 1,000,000 − 200,000
        assert f.nd == 800_000
        assert f.dv > 0  # positive delta velocity
        f.update(t0 + 60_000, 1_200_000, 2_400_000, 999.0)
        assert f.nd == -1_200_000 and f.dv < 0  # flow flipped bearish

    def test_rank_state_velocity_and_stability(self) -> None:
        r = flow.RankState()
        t0 = ms_at("09:20:00")
        r.apply(8, t0)
        r.apply(3, t0 + 60_000)  # improved 5 ranks
        assert r.rv() == 5.0 and r.overtakes == 1
        assert 0.0 <= r.rst(t0 + 120_000) <= 1.0

    def test_exit_score_directional(self) -> None:
        f = flow.SymbolFlow()
        t0 = ms_at("09:20:00")
        f.update(t0, 1_000_000, 1_000_000, 1000.0)
        f.update(t0 + 30_000, 1_000_000, 3_000_000, 990.0)  # strongly bearish flow
        r = flow.RankState()
        r.apply(2, t0)
        r.apply(9, t0 + 30_000)  # rank collapsed
        es_ce, parts = flow.exit_score(side="CE", flow=f, rank=r, rank_at_entry=2, cfg={})
        assert es_ce > 0.5 and parts["flip"] > 0 and parts["rank_loss"] > 0
        # The same conditions are FAVOURABLE for a PE leg
        es_pe, _ = flow.exit_score(side="PE", flow=f, rank=r, rank_at_entry=2, cfg={})
        assert es_pe < es_ce


class TestEngine:
    def _seed_then_signal(self, strat: Any, ctx: Any, memory: Any) -> Any:
        t0 = ms_at("09:20:00")
        chains0 = {s: _chain(s, ce_vol=1000, pe_vol=1000, ltp=100.0, ts=t0) for s in SYMBOLS}
        spots0 = dict.fromkeys(SYMBOLS, 1000.0)
        strat.on_tick(
            ctx, strat.build_snapshot(ctx, memory, _market(chains0, spots0, t0)), memory
        )  # baseline
        # 60s later: SYM0 gets massive CE buying + spot above session mean
        t1 = t0 + 60_000
        chains1 = {s: _chain(s, ce_vol=1000, pe_vol=1000, ltp=100.0, ts=t1) for s in SYMBOLS}
        chains1["SYM0"] = _chain("SYM0", ce_vol=500_000, pe_vol=1000, ltp=100.0, ts=t1)
        spots1 = dict.fromkeys(SYMBOLS, 1000.0)
        spots1["SYM0"] = 1010.0
        return (
            strat.on_tick(
                ctx, strat.build_snapshot(ctx, memory, _market(chains1, spots1, t1)), memory
            ),
            t1,
            chains1,
            spots1,
        )

    def test_bullish_leader_fires_ce(self) -> None:
        strat = RankMomentumStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        action, _t, _c, _s = self._seed_then_signal(strat, ctx, memory)
        assert action.kind == ActionKind.ENTER
        assert action.side == "CE"
        assert action.instrument_token.startswith("SYM0CE")
        assert action.snapshot["symbol"] == "SYM0"
        assert action.snapshot["scores"]["cs"] >= 0.30
        assert "SYM0:CE" in memory.legs

    def test_confidence_gate_blocks(self) -> None:
        strat = RankMomentumStrategy()
        ctx = _ctx(indicator={"confidence_min": 0.99})
        memory = strat.create_memory(ctx)
        action, _t, _c, _s = self._seed_then_signal(strat, ctx, memory)
        assert action.kind == ActionKind.NO_OP
        assert action.reason == "no_qualified_setup"

    def test_exit_score_closes_leg(self) -> None:
        strat = RankMomentumStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        _a, t1, chains1, spots1 = self._seed_then_signal(strat, ctx, memory)
        assert "SYM0:CE" in memory.legs
        # Flow reverses violently against the CE leg: huge PE volume + spot dump
        t2 = t1 + 30_000
        chains2 = dict(chains1)
        chains2["SYM0"] = _chain("SYM0", ce_vol=500_000, pe_vol=900_000, ltp=100.0, ts=t2)
        spots2 = dict(spots1)
        spots2["SYM0"] = 985.0
        action = strat.on_tick(
            ctx, strat.build_snapshot(ctx, memory, _market(chains2, spots2, t2)), memory
        )
        assert action.kind == ActionKind.EXIT
        assert action.reason in ("exit_score", "exit_sl")
        assert "SYM0:CE" not in memory.legs

    def test_eod_force_exit(self) -> None:
        strat = RankMomentumStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        _a, _t, chains1, spots1 = self._seed_then_signal(strat, ctx, memory)
        action = strat.on_tick(
            ctx,
            strat.build_snapshot(ctx, memory, _market(chains1, spots1, ms_at("15:30:01"))),
            memory,
        )
        assert action.kind == ActionKind.EXIT and action.reason == "exit_eod"


class TestMetadata:
    def test_name_description_and_own_exits(self) -> None:
        cfg = DEFAULT_STRATEGY_CONFIG_RANK_MOMENTUM
        assert cfg["name"] == "Rank Momentum"
        assert "exit" in cfg["description"].lower() or "Exit" in cfg["description"]
        assert cfg["exits"]["exit_score_threshold"] == 0.5
        assert cfg["indicator"]["confidence_min"] == 0.85
