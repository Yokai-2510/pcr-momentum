"""open_gainer_loser_v1 — universe session state machine tests.

Drives the strategy with synthetic universe views (spot map + per-symbol
chains) through: settlement snapshot capture -> open-window ranking ->
per-side entries (gainer CE / loser PE) -> one-shot semantics -> expiry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.init.redis_template import (
    DEFAULT_STRATEGY_CONFIG_OPEN_GL,
    UNIVERSE_INSTRUMENT_CONFIGS,
)
from engines.strategy.strategies.base import ActionKind, MarketView, VesselContext
from engines.strategy.strategies.nifty50_common import direction, ranking, selection
from engines.strategy.strategies.open_gainer_loser import (
    STRATEGY_DESCRIPTION,
    STRATEGY_ID,
    STRATEGY_NAME,
    OpenGainerLoserStrategy,
)

_IST = ZoneInfo("Asia/Kolkata")

SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]
STRIKES = [900, 950, 1000, 1050, 1100]


def ms_at(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(datetime(2026, 7, 6, h, m, s, tzinfo=_IST).timestamp() * 1000)


def _spot(
    symbol_pcts: dict[str, float], *, ts: int, volume: int = 1000
) -> dict[str, dict[str, Any]]:
    out = {}
    for sym, pct in symbol_pcts.items():
        prev = 1000.0
        out[sym] = {
            "ltp": prev * (1 + pct / 100),
            "prev_close": prev,
            "change_pct": pct,
            "volume": volume,
            "ts": ts,
        }
    return out


def _symbol_chain(sym: str, *, ltp: float, ts: int, strikes: list[int]) -> dict[str, Any]:
    return {
        str(s): {
            "ce": {"token": f"{sym}CE{s}", "ltp": ltp, "ts": ts},
            "pe": {"token": f"{sym}PE{s}", "ltp": ltp, "ts": ts},
        }
        for s in strikes
    }


def _market(
    spot: dict[str, dict[str, Any]],
    chains: dict[str, dict[str, Any]],
    now_ms: int,
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
    cfg = {**DEFAULT_STRATEGY_CONFIG_OPEN_GL}
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
        assert STRATEGY_NAME == "Open Gainer-Loser"
        assert "gainer" in STRATEGY_DESCRIPTION.lower()
        assert DEFAULT_STRATEGY_CONFIG_OPEN_GL["name"] == STRATEGY_NAME


class TestRankingShared:
    def test_valid_stocks_filters(self) -> None:
        spot = _spot({"RELIANCE": 2.0, "TCS": -25.0}, ts=1)  # TCS at circuit
        spot["INFY"] = {"ltp": 0, "prev_close": 100, "change_pct": 0, "volume": 5, "ts": 1}
        spot["HDFCBANK"] = {
            "ltp": 100,
            "prev_close": 100,
            "change_pct": 0,
            "volume": 0,  # suspended
            "ts": 1,
        }
        rows = ranking.valid_stocks(spot)
        assert [r["symbol"] for r in rows] == ["RELIANCE"]

    def test_rank_orders_both_sides(self) -> None:
        rows = ranking.valid_stocks(_spot({"A": 1.0, "B": -2.0, "C": 3.0}, ts=1))
        gainers, losers = ranking.rank(rows)
        assert gainers[0]["symbol"] == "C" and gainers[0]["rank"] == 1
        assert losers[0]["symbol"] == "B" and losers[0]["rank"] == 1


class TestSession:
    def test_full_session_both_sides_fire(self) -> None:
        strat = OpenGainerLoserStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)

        # 09:12 — snapshot capture (flat premiums baseline)
        t0 = ms_at("09:12:00")
        chains0 = {s: _symbol_chain(s, ltp=50.0, ts=t0, strikes=STRIKES) for s in SYMBOLS}
        spot0 = _spot(dict.fromkeys(SYMBOLS, 0.0), ts=t0)
        action = _tick(strat, ctx, memory, _market(spot0, chains0, t0))
        assert action.reason.startswith("settlement_snapshots_captured")
        assert memory.premium_snapshots is not None
        assert set(memory.premium_snapshots) == set(SYMBOLS)

        # 09:15:05 — RELIANCE +3% (top gainer, CE premiums risen -> BULLISH),
        # TCS -2.5% (top loser, PE premiums risen -> BEARISH)
        t1 = ms_at("09:15:05")
        spot1 = _spot({"RELIANCE": 3.0, "TCS": -2.5, "HDFCBANK": 0.5, "INFY": -0.5}, ts=t1)
        chains1 = {s: _symbol_chain(s, ltp=50.0, ts=t1, strikes=STRIKES) for s in SYMBOLS}
        for s in STRIKES:  # CE side up for RELIANCE
            chains1["RELIANCE"][str(s)]["ce"]["ltp"] = 55.0
        for s in STRIKES:  # PE side up for TCS
            chains1["TCS"][str(s)]["pe"]["ltp"] = 55.0

        market1 = _market(spot1, chains1, t1)

        # Tick 1 -> GAINER side fires: RELIANCE CE
        action = _tick(strat, ctx, memory, market1)
        assert action.kind == ActionKind.ENTER
        assert action.side == "CE"
        assert action.instrument_token.startswith("RELIANCECE")
        assert action.snapshot["category"] == "GAINER"
        assert action.snapshot["symbol"] == "RELIANCE"
        assert memory.category_state["GAINER"] == "entered"

        # Tick 2 -> LOSER side fires: TCS PE
        action = _tick(strat, ctx, memory, market1)
        assert action.kind == ActionKind.ENTER
        assert action.side == "PE"
        assert action.instrument_token.startswith("TCSPE")
        assert action.snapshot["category"] == "LOSER"

        # Tick 3 -> done for the day
        action = _tick(strat, ctx, memory, market1)
        assert action.kind == ActionKind.HOLD
        assert action.reason == "both_sides_done"

    def test_stale_stock_tick_blocks(self) -> None:
        strat = OpenGainerLoserStrategy()
        ctx = _ctx(direction_prediction={"mode": "basic", "neutral_fallback": "category"})
        memory = strat.create_memory(ctx)
        t = ms_at("09:15:10")
        stale = ms_at("09:14:00")
        spot = _spot({"RELIANCE": 3.0, "TCS": -2.0}, ts=stale)  # pre-open stock ticks
        chains = {s: _symbol_chain(s, ltp=50.0, ts=t, strikes=STRIKES) for s in SYMBOLS}
        action = _tick(strat, ctx, memory, _market(spot, chains, t))
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("waiting_first_live_stock_tick")

    def test_stale_option_tick_blocks(self) -> None:
        strat = OpenGainerLoserStrategy()
        ctx = _ctx(direction_prediction={"mode": "basic", "neutral_fallback": "category"})
        memory = strat.create_memory(ctx)
        t = ms_at("09:15:10")
        spot = _spot({"RELIANCE": 3.0, "TCS": -2.0}, ts=t)
        chains = {
            s: _symbol_chain(s, ltp=50.0, ts=ms_at("09:14:00"), strikes=STRIKES) for s in SYMBOLS
        }
        action = _tick(strat, ctx, memory, _market(spot, chains, t))
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("waiting_first_live_option_tick")

    def test_window_expiry_is_terminal(self) -> None:
        strat = OpenGainerLoserStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        t = ms_at("09:17:00")
        spot = _spot({"RELIANCE": 3.0, "TCS": -2.0}, ts=t)
        chains = {s: _symbol_chain(s, ltp=50.0, ts=t, strikes=STRIKES) for s in SYMBOLS}
        action = _tick(strat, ctx, memory, _market(spot, chains, t))
        assert action.reason.startswith("bootstrap_window_expired")
        assert memory.window_closed is True

    def test_universe_subscription_once(self) -> None:
        strat = OpenGainerLoserStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        market = _market(_spot(dict.fromkeys(SYMBOLS, 0.0), ts=1), {}, ms_at("09:00:00"))
        update = strat.update_universe(ctx, memory, market)
        assert update is not None
        assert len(update.subscribe) == len(SYMBOLS) * len(STRIKES)
        assert strat.update_universe(ctx, memory, market) is None


class TestSelectionOnStockChains:
    def test_itm_selection_on_symbol_chain(self) -> None:
        chain = {s: {"token": f"T{s}", "ltp": 50.0} for s in STRIKES}
        leaf, strike, _ = selection.select_strike(
            chain, spot=1000.0, moneyness="ITM", offset=0, option_type="CE"
        )
        assert leaf is not None and strike == 950

    def test_direction_uses_symbol_snapshot(self) -> None:
        snap = {"CE": dict.fromkeys(STRIKES, 50.0), "PE": dict.fromkeys(STRIKES, 50.0)}
        ce = {s: {"ltp": 55.0} for s in STRIKES}
        pe = {s: {"ltp": 50.0} for s in STRIKES}
        d, calc = direction.post_settlement_bias(
            snapshot=snap, ce_chain=ce, pe_chain=pe, spot=1000.0, bucket="ITM", strike_count=2
        )
        assert d == "BULLISH"
        assert calc["diff"] == 10.0
