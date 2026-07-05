"""The four pcr_analytics strategies: state machines + tick engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.init.redis_template import PCR_STRATEGY_CONFIGS, _pcr_instrument_config
from engines.strategy.strategies.base import ActionKind, VesselContext
from engines.strategy.strategies.oi_crossover import OiCrossoverStrategy
from engines.strategy.strategies.pcr_common.engine import IndexView
from engines.strategy.strategies.pcr_common.indicators import (
    LtpStrengthState,
    OiDiffState,
    VwapState,
)
from engines.strategy.strategies.vwap_band import VwapBandStrategy

_IST = ZoneInfo("Asia/Kolkata")
STRIKES = list(range(24750, 25251, 50))


def ms_at(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(datetime(2026, 7, 6, h, m, s, tzinfo=_IST).timestamp() * 1000)


def _view(
    now_ms: int,
    spot: float = 25000.0,
    *,
    ltp: float = 100.0,
    ce_oi: float = 1000,
    pe_oi: float = 1000,
    vol: float = 500,
) -> IndexView:
    ce = {
        s: {"token": f"CE{s}", "ltp": ltp, "oi": ce_oi, "vol": vol, "ts": now_ms} for s in STRIKES
    }
    pe = {
        s: {"token": f"PE{s}", "ltp": ltp, "oi": pe_oi, "vol": vol, "ts": now_ms} for s in STRIKES
    }
    return IndexView(
        instrument_id="nifty50",
        now_ms=now_ms,
        spot=spot,
        atm=int(round(spot / 50) * 50),
        ce=ce,
        pe=pe,
    )


def _ctx(sid: str) -> VesselContext:
    return VesselContext(
        strategy_id=sid,
        instrument_id="nifty50",
        strategy_config=dict(PCR_STRATEGY_CONFIGS[sid]),
        instrument_config=_pcr_instrument_config("nifty50"),
    )


class TestIndicators:
    def test_oi_diff_seed_and_crossover(self) -> None:
        st = OiDiffState()
        assert st.update(1000, 1000) == (None, None)  # first tick — no diff
        sig, diff = st.update(1000, 1200)  # PE building faster -> +ve -> BUY
        assert sig == "BUY" and diff == 200
        assert st.update(1000, 1300)[0] is None  # held regime, no re-emit
        sig, diff = st.update(1600, 1300)  # diff flips -ve -> SELL
        assert sig == "SELL" and diff == -300

    def test_vwap_band_crossover_semantics(self) -> None:
        st = VwapState()
        # seed: vwap == spot -> inside band -> no signal
        assert st.update(100.0, 10, 10) is None
        assert st.update(100.2, 20, 20) == "BUY"  # above band -> fresh BUY
        assert st.update(100.3, 30, 30) is None  # stays BUY, no re-emit
        assert st.update(100.12, 40, 40) is None  # dip inside band — no reset
        assert st.update(99.0, 500, 500) == "SELL"  # genuine flip

    def test_ltp_strict_conditions_and_regime(self) -> None:
        st = LtpStrengthState(rolling_ms=1000)
        t0 = ms_at("09:15:00")
        ce0 = {s: {"ltp": 100.0} for s in STRIKES}
        pe0 = {s: {"ltp": 100.0} for s in STRIKES}
        st.update(t0, 25000, 50, ce0, pe0, 25000.0, 24990.0)
        # 2s later: CE up, PE down, spot above vwap -> strict BUY
        ce1 = {s: {"ltp": 110.0} for s in STRIKES}
        pe1 = {s: {"ltp": 90.0} for s in STRIKES}
        sig, m = st.update(t0 + 2000, 25000, 50, ce1, pe1, 25010.0, 24990.0)
        assert sig == "BUY" and m["ce_sum"] > 0 and m["pe_sum"] < 0
        # same regime persists -> None
        sig, _ = st.update(t0 + 3000, 25000, 50, ce1, pe1, 25010.0, 24990.0)
        assert sig is None


class TestTickEngine:
    def test_enter_then_counter_crossover_exit_and_reopen(self) -> None:
        strat = OiCrossoverStrategy()
        ctx = _ctx("oi_crossover_v1")
        memory = strat.create_memory(ctx)

        t = ms_at("09:15:01")
        strat.on_tick(ctx, _view(t, ce_oi=1000, pe_oi=1000), memory)  # first tick seeds
        # PE OI builds -> BUY -> ENTER CE at ATM
        a = strat.on_tick(ctx, _view(t + 1000, ce_oi=1000, pe_oi=1400), memory)
        assert a.kind == ActionKind.ENTER and a.side == "CE" and a.strike == 25000
        assert memory.pos is not None and memory.pos.entry_ref == 100.0

        # Regime holds -> HOLD
        a = strat.on_tick(ctx, _view(t + 2000, ce_oi=1000, pe_oi=1500), memory)
        assert a.kind == ActionKind.HOLD

        # Counter-crossover (diff flips -ve) -> EXIT, reopen queued
        a = strat.on_tick(ctx, _view(t + 3000, ce_oi=2000, pe_oi=1200), memory)
        assert a.kind == ActionKind.EXIT and a.reason == "exit_crossover"
        assert memory.pending_side == "SELL" and memory.pos is None

        # Next tick -> queued PE entry
        a = strat.on_tick(ctx, _view(t + 4000, ce_oi=2100, pe_oi=1200), memory)
        assert a.kind == ActionKind.ENTER and a.side == "PE"

    def test_sl_exit_from_own_config(self) -> None:
        strat = OiCrossoverStrategy()
        ctx = _ctx("oi_crossover_v1")
        memory = strat.create_memory(ctx)
        t = ms_at("09:15:01")
        strat.on_tick(ctx, _view(t), memory)
        a = strat.on_tick(ctx, _view(t + 1000, ce_oi=1000, pe_oi=1400), memory)
        assert a.kind == ActionKind.ENTER
        # premium collapses 25% (own sl_pct=20) -> exit_sl
        a = strat.on_tick(ctx, _view(t + 2000, ltp=75.0, ce_oi=1000, pe_oi=1400), memory)
        assert a.kind == ActionKind.EXIT and a.reason == "exit_sl"

    def test_eod_force_exit(self) -> None:
        strat = VwapBandStrategy()
        ctx = _ctx("vwap_band_v1")
        memory = strat.create_memory(ctx)
        t = ms_at("09:15:01")
        strat.on_tick(ctx, _view(t, spot=25000.0), memory)
        a = strat.on_tick(ctx, _view(t + 1000, spot=25100.0), memory)  # above band
        assert a.kind == ActionKind.ENTER and a.side == "CE"
        a = strat.on_tick(ctx, _view(ms_at("15:30:00"), spot=25100.0), memory)
        assert a.kind == ActionKind.EXIT and a.reason == "exit_eod"

    def test_dynamic_atm_subscription_shifts(self) -> None:
        from engines.strategy.strategies.base import MarketView

        strat = OiCrossoverStrategy()
        ctx = _ctx("oi_crossover_v1")
        memory = strat.create_memory(ctx)
        chain = {
            str(s): {"ce": {"token": f"CE{s}"}, "pe": {"token": f"PE{s}"}}
            for s in range(24000, 26001, 50)
        }

        def mk(spot: float, now: int) -> MarketView:
            return MarketView(
                chain=chain,
                spot={"ltp": spot},
                meta={},
                now_ms=now,
                token_lookup=lambda s, o: (chain.get(str(s)) or {}).get(o.lower(), {}).get("token"),
            )

        u1 = strat.update_universe(ctx, memory, mk(25000.0, 1_000))
        assert u1 is not None and "CE25000" in u1.subscribe and len(u1.subscribe) == 30
        # same ATM -> None; new ATM within hysteresis -> None; after -> shift
        assert strat.update_universe(ctx, memory, mk(25010.0, 2_000)) is None
        assert strat.update_universe(ctx, memory, mk(25060.0, 3_000)) is None
        u2 = strat.update_universe(ctx, memory, mk(25060.0, 10_000))
        assert u2 is not None and u2.basket_view == {"atm": 25050, "range": 7}


class TestMetadata:
    def test_names_and_descriptions(self) -> None:
        for sid in ("oi_crossover_v1", "volume_diff_v1", "vwap_band_v1", "ltp_strength_v1"):
            cfg = PCR_STRATEGY_CONFIGS[sid]
            assert cfg["name"] and cfg["description"]
            assert "exits" in cfg and "sl_pct" in cfg["exits"]  # own exit stack


def _unused(x: Any) -> Any:  # keep linters quiet about fixtures-by-name
    return x
