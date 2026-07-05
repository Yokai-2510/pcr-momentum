"""bootstrap_momentum_v1 — full pipeline tests against the ported logic.

Times are constructed as IST epoch-ms so the session state machine
(snapshot capture -> open window -> one-shot fire -> expiry) is driven
deterministically without a clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.init.redis_template import (
    BOOTSTRAP_INSTRUMENT_CONFIGS,
    DEFAULT_STRATEGY_CONFIG_BOOTSTRAP,
)
from engines.strategy.strategies.base import ActionKind, MarketView, VesselContext
from engines.strategy.strategies.bootstrap_momentum import (
    BootstrapMomentumStrategy,
    direction,
    selection,
)

_IST = ZoneInfo("Asia/Kolkata")


def ms_at(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(datetime(2026, 7, 6, h, m, s, tzinfo=_IST).timestamp() * 1000)


def _leaf(token: str, ltp: float, ts: int) -> dict[str, Any]:
    return {"token": token, "ltp": ltp, "bid": ltp - 0.5, "ask": ltp + 0.5, "ts": ts}


def _chain(
    strikes: list[int], *, ltp_by_strike: dict[int, tuple[float, float]], ts: int
) -> dict[str, Any]:
    """Platform-shaped chain: {strike_str: {ce: leaf, pe: leaf}}."""
    out: dict[str, Any] = {}
    for s in strikes:
        ce_ltp, pe_ltp = ltp_by_strike[s]
        out[str(s)] = {
            "ce": _leaf(f"CE{s}", ce_ltp, ts),
            "pe": _leaf(f"PE{s}", pe_ltp, ts),
        }
    return out


STRIKES = list(range(24700, 25301, 50))  # 13 strikes, ATM=25000 at spot 25000


def _flat_premiums(ce: float = 100.0, pe: float = 100.0) -> dict[int, tuple[float, float]]:
    return {s: (ce, pe) for s in STRIKES}


def _market(chain: dict[str, Any], spot: float, now_ms: int) -> MarketView:
    def _lookup(strike: int, side: str) -> str | None:
        leaf = (chain.get(str(strike)) or {}).get(side.lower())
        return leaf["token"] if isinstance(leaf, dict) else None

    return MarketView(chain=chain, spot={"ltp": spot}, meta={}, now_ms=now_ms, token_lookup=_lookup)


def _ctx(**strategy_overrides: Any) -> VesselContext:
    cfg = {**DEFAULT_STRATEGY_CONFIG_BOOTSTRAP}
    for key, value in strategy_overrides.items():
        cfg[key] = value
    return VesselContext(
        strategy_id="bootstrap_momentum_v1",
        instrument_id="nifty50",
        strategy_config=cfg,
        instrument_config=dict(BOOTSTRAP_INSTRUMENT_CONFIGS["nifty50"]),
    )


# ── direction module ────────────────────────────────────────────────────────


class TestDirection:
    def test_bucket_collection_semantics(self) -> None:
        chain = {s: {"ltp": 1.0} for s in STRIKES}
        # ATM = single ATM strike
        atm = direction.collect_bucket_strikes(chain, 25000, "ATM", 3, "CE")
        assert [s for s, _ in atm] == [25000]
        # CE OTM = higher strikes going outward
        ce_otm = direction.collect_bucket_strikes(chain, 25000, "OTM", 3, "CE")
        assert [s for s, _ in ce_otm] == [25050, 25100, 25150]
        # PE OTM = lower strikes going outward
        pe_otm = direction.collect_bucket_strikes(chain, 25000, "OTM", 3, "PE")
        assert [s for s, _ in pe_otm] == [24950, 24900, 24850]
        # CE ITM = lower strikes; PE ITM = higher strikes
        assert [s for s, _ in direction.collect_bucket_strikes(chain, 25000, "ITM", 2, "CE")] == [
            24950,
            24900,
        ]
        assert [s for s, _ in direction.collect_bucket_strikes(chain, 25000, "ITM", 2, "PE")] == [
            25050,
            25100,
        ]

    def test_bias_bullish_when_ce_rises_more(self) -> None:
        snap = {"CE": {s: 100.0 for s in STRIKES}, "PE": {s: 100.0 for s in STRIKES}}
        ce_chain = {s: {"ltp": 105.0} for s in STRIKES}  # CE +5%
        pe_chain = {s: {"ltp": 101.0} for s in STRIKES}  # PE +1%
        d, calc = direction.post_settlement_bias(
            snapshot=snap,
            ce_chain=ce_chain,
            pe_chain=pe_chain,
            spot=25000,
            bucket="ITM",
            strike_count=3,
            threshold_pct=0.5,
        )
        assert d == "BULLISH"
        assert calc["diff"] == 4.0

    def test_bias_neutral_inside_threshold_and_no_snapshot(self) -> None:
        snap = {"CE": {s: 100.0 for s in STRIKES}, "PE": {s: 100.0 for s in STRIKES}}
        ce_chain = {s: {"ltp": 100.2} for s in STRIKES}
        pe_chain = {s: {"ltp": 100.0} for s in STRIKES}
        d, _ = direction.post_settlement_bias(
            snapshot=snap,
            ce_chain=ce_chain,
            pe_chain=pe_chain,
            spot=25000,
            bucket="ATM",
            strike_count=1,
            threshold_pct=0.5,
        )
        assert d == "NEUTRAL"
        d2, calc2 = direction.post_settlement_bias(
            snapshot=None, ce_chain=ce_chain, pe_chain=pe_chain, spot=25000
        )
        assert d2 == "NEUTRAL" and calc2["reason"] == "no_snapshot"

    def test_neutral_fallback_policies(self) -> None:
        assert (
            direction.resolve_option_type("NEUTRAL", category="GAINER", neutral_fallback="category")
            == "CE"
        )
        assert (
            direction.resolve_option_type("NEUTRAL", category="LOSER", neutral_fallback="category")
            == "PE"
        )
        assert (
            direction.resolve_option_type("NEUTRAL", category="GAINER", neutral_fallback="skip")
            is None
        )
        assert (
            direction.resolve_option_type("BEARISH", category="GAINER", neutral_fallback="skip")
            == "PE"
        )

    def test_smoothing_majority_vote(self) -> None:
        hist = ["BULLISH", "BULLISH", "BEARISH"]
        assert direction.apply_smoothing("BEARISH", hist, enabled=True, periods=3) == "BULLISH"
        assert direction.apply_smoothing("BEARISH", hist, enabled=False, periods=3) == "BEARISH"


# ── selection module ─────────────────────────────────────────────────────────


class TestSelection:
    def _chain(self) -> dict[int, dict[str, Any]]:
        return {s: {"token": f"T{s}", "ltp": 100.0} for s in STRIKES}

    def test_itm_offset_symmetry(self) -> None:
        chain = self._chain()
        # CE ITM offset 2 -> 3 strikes BELOW ATM
        _, strike_ce, _ = selection.select_strike(
            chain, spot=25000, moneyness="ITM", offset=2, option_type="CE"
        )
        assert strike_ce == 24850
        # PE ITM offset 2 -> 3 strikes ABOVE ATM
        _, strike_pe, _ = selection.select_strike(
            chain, spot=25000, moneyness="ITM", offset=2, option_type="PE"
        )
        assert strike_pe == 25150

    def test_otm_and_atm(self) -> None:
        chain = self._chain()
        _, s1, _ = selection.select_strike(
            chain, spot=25000, moneyness="OTM", offset=0, option_type="CE"
        )
        assert s1 == 25050
        _, s2, _ = selection.select_strike(
            chain, spot=25000, moneyness="ATM", offset=0, option_type="PE"
        )
        assert s2 == 25000

    def test_out_of_bounds_clamps_to_atm(self) -> None:
        chain = self._chain()
        _, strike, _ = selection.select_strike(
            chain, spot=25000, moneyness="OTM", offset=50, option_type="CE"
        )
        assert strike == 25000

    def test_validation_and_premium_filter(self) -> None:
        leaf_dead = {"token": "T", "ltp": 0.0}
        got, _, reason = selection.select_strike(
            {25000: leaf_dead}, spot=25000, moneyness="ATM", offset=0, option_type="CE"
        )
        assert got is None and reason == "OPTION_LTP_ZERO"

        ok, reason = selection.premium_filter(
            {"ltp": 30.0}, {"enabled": True, "min_ltp": 40, "max_ltp": 450}
        )
        assert not ok and reason.startswith("PREMIUM_TOO_LOW")
        ok, reason = selection.premium_filter(
            {"ltp": 500.0}, {"enabled": True, "min_ltp": 40, "max_ltp": 450}
        )
        assert not ok and reason.startswith("PREMIUM_TOO_HIGH")
        ok, _ = selection.premium_filter({"ltp": 500.0}, {"enabled": False})
        assert ok


# ── full strategy session state machine ─────────────────────────────────────


class TestBootstrapSession:
    def _run_tick(self, strat: Any, ctx: Any, memory: Any, market: MarketView) -> Any:
        snap = strat.build_snapshot(ctx, memory, market)
        return strat.on_tick(ctx, snap, memory)

    def test_full_session_bullish_fire(self) -> None:
        strat = BootstrapMomentumStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)

        # 09:12 — pre-open tick: settlement snapshot captured
        t0 = ms_at("09:12:00")
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t0), 25000.0, t0)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.NO_OP
        assert action.reason == "settlement_snapshot_captured"
        assert memory.premium_snapshot is not None

        # 09:15:05 — CE premiums +5%, fresh post-open ticks -> BULLISH fire
        t1 = ms_at("09:15:05")
        risen = {s: (105.0, 100.0) for s in STRIKES}
        market = _market(_chain(STRIKES, ltp_by_strike=risen, ts=t1), 25000.0, t1)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.ENTER
        assert action.side == "CE"
        # ITM offset 2 for CE = 3 strikes below ATM
        assert action.strike == 24850
        assert action.instrument_token == "CE24850"
        assert action.qty_lots == 1
        # Full audit trail on the snapshot channel (A8)
        assert action.snapshot is not None
        assert action.snapshot["direction"] == "BULLISH"
        assert action.snapshot["bias_calculation"]["diff"] == 5.0
        assert action.snapshot["selection"]["strike"] == 24850

        # One-shot: next tick holds, never re-enters
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.HOLD
        assert action.reason == "bootstrap_done"

    def test_stale_option_tick_blocks_until_fresh(self) -> None:
        strat = BootstrapMomentumStrategy()
        ctx = _ctx(direction_prediction={"mode": "fixed", "fixed_side": "PE"})
        memory = strat.create_memory(ctx)

        # 09:15:10 but leaves carry PRE-OPEN timestamps -> must wait
        t = ms_at("09:15:10")
        stale_ts = ms_at("09:14:30")
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=stale_ts), 25000.0, t)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.NO_OP
        assert action.reason == "waiting_first_live_option_tick"
        assert memory.entered is False

        # Same second, fresh tick arrives -> fires PE at ITM offset 2 (above ATM)
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25000.0, t)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.ENTER
        assert action.side == "PE"
        assert action.strike == 25150

    def test_window_expires_after_60s(self) -> None:
        strat = BootstrapMomentumStrategy()
        ctx = _ctx(direction_prediction={"mode": "fixed", "fixed_side": "CE"})
        memory = strat.create_memory(ctx)

        t = ms_at("09:16:30")  # 90s after open
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25000.0, t)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("bootstrap_window_expired")
        assert memory.window_closed is True

        # Even a perfect tick inside a later evaluation cannot fire.
        action = self._run_tick(strat, ctx, memory, market)
        assert action.reason == "bootstrap_window_closed"

    def test_neutral_skip_policy_retries_without_firing(self) -> None:
        strat = BootstrapMomentumStrategy()
        dir_cfg = dict(DEFAULT_STRATEGY_CONFIG_BOOTSTRAP["direction_prediction"])
        dir_cfg["neutral_fallback"] = "skip"
        ctx = _ctx(direction_prediction=dir_cfg)
        memory = strat.create_memory(ctx)
        # No settlement snapshot ever captured -> bias NEUTRAL -> skip
        t = ms_at("09:15:05")
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25000.0, t)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.NO_OP
        assert action.reason == "DIRECTION_NEUTRAL_SKIP"
        assert memory.entered is False and memory.window_closed is False

    def test_premium_filter_rejection_is_terminal(self) -> None:
        strat = BootstrapMomentumStrategy()
        ctx = _ctx(
            direction_prediction={"mode": "fixed", "fixed_side": "CE"},
            filters={"premium": {"enabled": True, "min_ltp": 200, "max_ltp": 450}},
        )
        memory = strat.create_memory(ctx)
        t = ms_at("09:15:05")
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25000.0, t)
        action = self._run_tick(strat, ctx, memory, market)
        assert action.kind == ActionKind.NO_OP
        assert action.reason.startswith("PREMIUM_TOO_LOW")
        assert memory.window_closed is True  # original marks the slot handled

    def test_universe_subscribes_and_freezes_after_entry(self) -> None:
        strat = BootstrapMomentumStrategy()
        ctx = _ctx(direction_prediction={"mode": "fixed", "fixed_side": "CE"})
        memory = strat.create_memory(ctx)
        t = ms_at("09:12:00")
        market = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25000.0, t)

        update = strat.update_universe(ctx, memory, market)
        assert update is not None
        # ATM 25000 ± 6 strikes x 2 sides, clipped to the available chain
        assert "CE25000" in update.subscribe and "PE24700" in update.subscribe
        assert update.basket_view == {"atm": 25000, "range": 6}
        # Same ATM again -> no change
        assert strat.update_universe(ctx, memory, market) is None

        memory.entered = True
        # Spot moves to a new ATM but the universe is frozen post-entry.
        moved = _market(_chain(STRIKES, ltp_by_strike=_flat_premiums(), ts=t), 25100.0, t)
        assert strat.update_universe(ctx, memory, moved) is None
