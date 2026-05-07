"""`Signal` — Strategy Engine emits, Order Exec consumes.

Per `docs/Schema.md` §5 and `docs/Strategy.md` §9.4 (v2 payload). A signal
is the commitment to enter / flip / exit a position; once published, the
ownership of the trade lifecycle moves to Order Exec.

v2 changes (from premium-diff v1):
  + strategy_id, instrument_id      multi-strategy attribution
  + score, score_breakdown          quality score from §4.8
  + net_pressure_at_signal          Strategy.md §4.7
  + decision_ts                     ms since epoch
  - Legacy premium-diff fields kept ONLY for backward compat with the
    existing dispatcher; will be removed in Phase F when premium_diff/ is
    deleted.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SignalIntent(StrEnum):
    """Why the strategy emitted this signal."""

    FRESH_ENTRY = "FRESH_ENTRY"
    REVERSAL_FLIP = "REVERSAL_FLIP"
    MANUAL_EXIT = "MANUAL_EXIT"  # used for strategy-driven EXIT signals (Strategy.md §5.3)


class Signal(BaseModel):
    """A trade intent published by Strategy → consumed by Order Exec.

    v2 schema. Order Exec dispatcher reads `strategy_id` + `instrument_id`
    and threads them through the trade record so PnL/attribution per
    strategy is a free GROUP BY.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sig_id: str = Field(..., description="Monotonic id; sha256 of canonical fields")
    strategy_id: str = Field(..., description="e.g. 'bid_ask_imbalance_v1'")
    instrument_id: Literal["nifty50", "banknifty", "sensex"] = Field(
        ..., description="Index identifier; must match a vessel"
    )
    # Legacy alias for backward-compat with order_exec/dispatcher.py and
    # views that read `index`. Deprecated; use `instrument_id`.
    index: Literal["nifty50", "banknifty", "sensex"] = Field(
        ..., description="Legacy alias of instrument_id"
    )
    side: Literal["CE", "PE"]
    strike: int = Field(..., description="Selected strike")
    instrument_token: str = Field(..., description="Broker instrument key, e.g. 'NSE_FO|49520'")
    intent: SignalIntent
    qty_lots: int = Field(..., gt=0, description="Number of lots; lot_size lives in instrument_config")

    # New v2 fields
    score: float | None = Field(default=None, description="Quality score 0-10 (Strategy.md §4.8)")
    score_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Per-condition score breakdown"
    )
    net_pressure_at_signal: float | None = Field(
        default=None, description="Net pressure (cum_ce - cum_pe) at decision time"
    )
    decision_ts: int = Field(..., description="ms since epoch when the strategy decided")

    # Legacy premium-diff fields (kept until order_exec is migrated; safe defaults)
    diff_at_signal: float = Field(default=0.0, description="DEPRECATED — premium-diff legacy")
    sum_ce_at_signal: float = Field(default=0.0, description="DEPRECATED — premium-diff legacy")
    sum_pe_at_signal: float = Field(default=0.0, description="DEPRECATED — premium-diff legacy")
    delta_at_signal: float = Field(default=0.0, description="DEPRECATED — premium-diff legacy")
    delta_pcr_at_signal: float | None = Field(default=None, description="DEPRECATED")
    strategy_version: str = Field(default="", description="DEPRECATED — use strategy_id")

    ts: datetime = Field(..., description="Emission timestamp (UTC ISO-8601)")
