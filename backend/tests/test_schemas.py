"""Round-trip tests for every Pydantic model in `state/schemas/`.

Phase-1 exit criterion: "All Pydantic schemas serialize/deserialize
round-trip in tests." Each model gets at least one happy construction +
JSON round-trip + (where applicable) one validation-failure case.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from state.schemas import (
    CapitalView,
    ClosedPositionReport,
    ConfigsView,
    DashboardView,
    DayPnL,
    DeltaPCRCumulative,
    DeltaPCRHistoryEntry,
    DeltaPCRInterval,
    DeltaPCRView,
    DependencyStatus,
    EngineStatus,
    ExecutionConfig,
    ExitProfile,
    ExitReason,
    HealthSummary,
    HealthView,
    IndexConfig,
    IndexMeta,
    Latencies,
    MarketSnapshot,
    OptionChainEntry,
    OptionContract,
    OrderEvent,
    OrderEventEntry,
    OrderEventType,
    PerIndexPnL,
    PnLBreakdown,
    PnLView,
    Position,
    PositionStage,
    PositionView,
    RiskConfig,
    SessionConfig,
    Signal,
    SignalIntent,
    StrategyView,
)


def _round_trip(model: object) -> object:
    """Serialize → JSON → deserialize and assert equality."""
    cls = type(model)
    payload = cls.model_validate_json(
        model.model_dump_json()  # type: ignore[attr-defined]
    )
    assert payload == model
    return payload


# ---------------------------------------------------------------------------
# signal
# ---------------------------------------------------------------------------
def _signal() -> Signal:
    return Signal(
        sig_id="nifty50_1",
        index="nifty50",
        side="CE",
        strike=24500,
        instrument_token="NSE_FO|49520",
        intent=SignalIntent.FRESH_ENTRY,
        qty_lots=1,
        diff_at_signal=12.5,
        sum_ce_at_signal=120.0,
        sum_pe_at_signal=85.0,
        delta_at_signal=-35.0,
        delta_pcr_at_signal=0.92,
        strategy_version="abc1234",
        ts=datetime(2026, 4, 28, 9, 20, tzinfo=UTC),
    )


def test_signal_round_trip() -> None:
    _round_trip(_signal())


def test_signal_rejects_bad_index() -> None:
    with pytest.raises(ValidationError):
        Signal(
            sig_id="x",
            index="sensex",  # type: ignore[arg-type]
            side="CE",
            strike=1,
            instrument_token="t",
            intent=SignalIntent.FRESH_ENTRY,
            qty_lots=1,
            diff_at_signal=0,
            sum_ce_at_signal=0,
            sum_pe_at_signal=0,
            delta_at_signal=0,
            strategy_version="x",
            ts=datetime.now(UTC),
        )


def test_signal_extra_field_forbidden() -> None:
    payload = _signal().model_dump()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        Signal.model_validate(payload)


# ---------------------------------------------------------------------------
# order_event
# ---------------------------------------------------------------------------
def test_order_event_round_trip() -> None:
    ev = OrderEvent(
        event_type=OrderEventType.ACK,
        order_id="BR-1",
        position_id="p1",
        sig_id="nifty50_1",
        index="nifty50",
        instrument_token="NSE_FO|49520",
        side="CE",
        qty=75,
        filled_qty=0,
        price=158.0,
        avg_price=None,
        broker_status="OPEN",
        ts=datetime.now(UTC),
        internal_latency_ms=12,
    )
    _round_trip(ev)


# ---------------------------------------------------------------------------
# position
# ---------------------------------------------------------------------------
def _position() -> Position:
    profile = ExitProfile(
        sl_pct=0.30,
        target_pct=0.60,
        tsl_arm_pct=0.20,
        tsl_trail_pct=0.10,
        max_hold_sec=900,
    )
    now = datetime.now(UTC)
    return Position(
        pos_id="p1",
        sig_id="nifty50_1",
        index="nifty50",
        side="CE",
        strike=24500,
        instrument_token="NSE_FO|49520",
        qty=75,
        entry_order_id="BR-1",
        entry_price=158.0,
        entry_ts=now,
        mode="paper",
        intent="FRESH_ENTRY",
        sl_level=110.6,
        target_level=252.8,
        tsl_arm_pct=0.20,
        tsl_trail_pct=0.10,
        peak_premium=158.0,
        current_premium=158.0,
        exit_profile=profile,
        sum_ce_at_entry=120.0,
        sum_pe_at_entry=85.0,
        strategy_version="abc1234",
    )


def test_position_round_trip() -> None:
    _round_trip(_position())


def test_position_stage_enum_complete() -> None:
    expected = {
        "GATE_PREENTRY",
        "ENTRY_SUBMITTING",
        "ENTRY_OPEN",
        "ENTRY_FILLED",
        "EXIT_EVAL",
        "EXIT_SUBMITTING",
        "EXIT_OPEN",
        "EXIT_FILLED",
        "REPORTING",
        "CLEANUP",
        "DONE",
        "ABORTED",
    }
    assert {s.value for s in PositionStage} == expected


# ---------------------------------------------------------------------------
# pnl
# ---------------------------------------------------------------------------
def test_pnl_round_trip() -> None:
    _round_trip(PerIndexPnL(realized=100.0, unrealized=-50.0, trades_count=3, win_rate=0.66))
    _round_trip(
        DayPnL(
            realized=300.0,
            unrealized=20.0,
            trade_count=5,
            win_rate=0.4,
            day_pnl_pct_of_capital=0.0015,
            ts=datetime.now(UTC),
        )
    )


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _market_snapshot(ts: datetime) -> MarketSnapshot:
    return MarketSnapshot(
        ts=ts,
        spot=24500.0,
        sum_ce=120.0,
        sum_pe=85.0,
        delta=-35.0,
        delta_pcr_cumulative=0.92,
        per_strike={"24500": {"ce_ltp": 158.0, "pe_ltp": 78.0}},
    )


def test_closed_position_report_round_trip() -> None:
    now = datetime.now(UTC)
    report = ClosedPositionReport(
        sig_id="nifty50_1",
        index="nifty50",
        mode="paper",
        side="CE",
        strike=24500,
        instrument_token="NSE_FO|49520",
        qty=75,
        entry_ts=now,
        exit_ts=now,
        holding_seconds=120,
        entry_price=158.0,
        exit_price=170.0,
        pnl=900.0,
        pnl_pct=0.076,
        exit_reason=ExitReason.HARD_TARGET,
        intent="FRESH_ENTRY",
        signal_snapshot={"sig_id": "nifty50_1"},
        pre_open_snapshot={"24500": {"ce_premium": 156.0}},
        market_snapshot_entry=_market_snapshot(now),
        market_snapshot_exit=_market_snapshot(now),
        order_events=[
            OrderEventEntry(
                ts=now,
                event_type="ACK",
                order_id="BR-1",
                qty=75,
                price=158.0,
                broker_status="OPEN",
            )
        ],
        latencies=Latencies(
            signal_to_submit_ms=10,
            submit_to_ack_ms=20,
            ack_to_fill_ms=30,
            decision_to_exit_submit_ms=40,
            exit_submit_to_fill_ms=50,
        ),
        pnl_breakdown=PnLBreakdown(gross=950.0, charges=30.0, slippage=20.0, net=900.0),
        delta_pcr_at_entry=0.92,
        delta_pcr_at_exit=0.95,
        strategy_version="abc1234",
    )
    _round_trip(report)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
def test_health_round_trip() -> None:
    now = datetime.now(UTC)
    summary = HealthSummary(
        summary="OK",
        engines={"init": EngineStatus(alive=True, last_hb_ts=now)},
        dependencies={"redis": DependencyStatus(name="redis", status="OK", last_probe_ts=now)},
        auth="valid",
        ts=now,
    )
    _round_trip(summary)


# ---------------------------------------------------------------------------
# delta_pcr
# ---------------------------------------------------------------------------
def test_delta_pcr_round_trip() -> None:
    now = datetime.now(UTC)
    interval = DeltaPCRInterval(
        interval_pcr=0.92, total_d_put=2000, total_d_call=2200, atm=24500, ts=now
    )
    cumulative = DeltaPCRCumulative(
        cumulative_pcr=0.95, cumulative_d_put=10000, cumulative_d_call=10500, ts=now
    )
    _round_trip(interval)
    _round_trip(cumulative)
    _round_trip(DeltaPCRHistoryEntry(interval=interval, cumulative=cumulative))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_execution_config_round_trip() -> None:
    cfg = ExecutionConfig(
        buffer_inr=2,
        eod_buffer_inr=5,
        spread_skip_pct=0.05,
        drift_threshold_inr=3,
        chase_ceiling_inr=15,
        open_timeout_sec=8,
        partial_grace_sec=3,
        max_retries=2,
        worker_pool_size=8,
        liquidity_exit_suppress_after="15:00",
    )
    _round_trip(cfg)


def test_session_config_defaults() -> None:
    cfg = SessionConfig()
    assert cfg.market_open == "09:15"
    _round_trip(cfg)


def test_risk_config_round_trip() -> None:
    cfg = RiskConfig(
        daily_loss_circuit_pct=0.08, max_concurrent_positions=2, trading_capital_inr=200000
    )
    _round_trip(cfg)


def _index_config() -> IndexConfig:
    return IndexConfig(
        index="nifty50",
        strike_step=50,
        lot_size=75,
        pre_open_subscribe_window=6,
        trading_basket_range=3,
        reversal_threshold_inr=20,
        entry_dominance_threshold_inr=20,
        post_sl_cooldown_sec=60,
        post_reversal_cooldown_sec=90,
        max_entries_per_day=8,
        max_reversals_per_day=4,
        qty_lots=1,
        sl_pct=0.30,
        target_pct=0.60,
        tsl_arm_pct=0.20,
        tsl_trail_pct=0.10,
        max_hold_sec=900,
    )


def test_index_config_round_trip() -> None:
    _round_trip(_index_config())


# ---------------------------------------------------------------------------
# instruments
# ---------------------------------------------------------------------------
def test_option_contract_round_trip() -> None:
    c = OptionContract(
        instrument_token="NSE_FO|49520",
        symbol="NIFTY24500CE",
        underlying="nifty50",
        expiry=date(2026, 5, 1),
        strike=24500,
        type="CE",
        lot_size=75,
        tick_size=0.05,
    )
    _round_trip(c)


def test_index_meta_round_trip() -> None:
    m = IndexMeta(
        index="nifty50",
        strike_step=50,
        lot_size=75,
        spot_token="NSE_INDEX|Nifty 50",
        expiry=date(2026, 5, 1),
        prev_close=24500.0,
        atm_at_open=24500,
        ce_strikes=[24450, 24500, 24550],
        pe_strikes=[24450, 24500, 24550],
    )
    _round_trip(m)


def test_option_chain_entry_round_trip() -> None:
    e = OptionChainEntry(token="NSE_FO|49520", ltp=158.0, bid=157.5, ask=158.5, oi=67800)
    _round_trip(e)


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------
def test_view_round_trip() -> None:
    now = datetime.now(UTC)
    sv = StrategyView(
        index="nifty50",
        enabled=True,
        state="FLAT",
        sum_ce=120.0,
        sum_pe=85.0,
        delta=-35.0,
        diffs={"24500": 12.5},
        ts=now,
    )
    _round_trip(sv)

    pv = PositionView(
        index="nifty50",
        has_open=False,
        ts=now,
    )
    _round_trip(pv)

    dpv = DeltaPCRView(index="nifty50", interval_pcr=0.92, cumulative_pcr=0.95, ts=now)
    _round_trip(dpv)

    pnl_v = PnLView(
        day=DayPnL(realized=0, unrealized=0, trade_count=0, win_rate=0, day_pnl_pct_of_capital=0),
        per_index={
            "nifty50": PerIndexPnL(),
            "banknifty": PerIndexPnL(),
        },
        ts=now,
    )
    _round_trip(pnl_v)

    cv = CapitalView(available=200000, used=0, deployed=0, ts=now)
    _round_trip(cv)

    hv = HealthView(
        summary=HealthSummary(summary="OK", auth="valid", ts=now),
        ts=now,
    )
    _round_trip(hv)

    cfgv = ConfigsView(
        execution={}, session={}, risk={}, indexes={"nifty50": {}, "banknifty": {}}, ts=now
    )
    _round_trip(cfgv)

    dash = DashboardView(
        trading_active=True,
        trading_disabled_reason="none",
        mode="paper",
        auth_status="valid",
        health_summary="OK",
        pnl=DayPnL(realized=0, unrealized=0, trade_count=0, win_rate=0, day_pnl_pct_of_capital=0),
        open_positions_count=0,
        ts=now,
    )
    _round_trip(dash)
