"""`ClosedPositionReport` — the durable post-trade record.

Per `docs/Schema.md` §2.4 (`trades_closed_positions`) + §5. Order Exec
builds one report per closed position and persists it to Postgres
(forensic-grade audit trail).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from state.schemas.position import ExitReason


class MarketSnapshot(BaseModel):
    """Per-strike market snapshot embedded in a closed-position report."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    spot: float
    sum_ce: float
    sum_pe: float
    delta: float
    delta_pcr_cumulative: float | None = None
    per_strike: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="{strike: {ce_ltp, pe_ltp, ce_oi, pe_oi, ...}}",
    )


class OrderEventEntry(BaseModel):
    """One row of the order-events trail captured during the position."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    event_type: str
    order_id: str
    qty: int | None = None
    price: float | None = None
    broker_status: str | None = None
    note: str | None = None


class Latencies(BaseModel):
    """End-to-end latency breakdown captured per closed position."""

    model_config = ConfigDict(extra="forbid")

    signal_to_submit_ms: int
    submit_to_ack_ms: int
    ack_to_fill_ms: int
    decision_to_exit_submit_ms: int
    exit_submit_to_fill_ms: int


class PnLBreakdown(BaseModel):
    """Decomposition of net PnL into gross/charges/slippage."""

    model_config = ConfigDict(extra="forbid")

    gross: float
    charges: float
    slippage: float
    net: float


class ClosedPositionReport(BaseModel):
    """Mirrors `trades_closed_positions` row exactly (Schema.md §2.4)."""

    model_config = ConfigDict(extra="forbid")

    sig_id: str
    index: Literal["nifty50", "banknifty"]
    mode: Literal["paper", "live"]
    side: Literal["CE", "PE"]
    strike: int
    instrument_token: str
    qty: int = Field(..., gt=0)
    entry_ts: datetime
    exit_ts: datetime
    holding_seconds: int = Field(..., ge=0)
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    exit_reason: ExitReason
    intent: Literal["FRESH_ENTRY", "REVERSAL_FLIP"]
    signal_snapshot: dict[str, Any]
    pre_open_snapshot: dict[str, Any]
    market_snapshot_entry: MarketSnapshot
    market_snapshot_exit: MarketSnapshot
    exit_eval_history: list[dict[str, Any]] | None = None
    trailing_history: list[dict[str, Any]] | None = None
    order_events: list[OrderEventEntry]
    latencies: Latencies
    pnl_breakdown: PnLBreakdown
    delta_pcr_at_entry: float | None = None
    delta_pcr_at_exit: float | None = None
    raw_broker_responses: dict[str, Any] | None = None
    strategy_version: str
