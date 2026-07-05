"""`Signal` — Strategy Engine emits, Order Exec consumes.

Per `docs/Schema.md` §5 and `docs/Strategy.md` §9.4 (v2 payload). A signal
is the commitment to enter / flip / exit a position; once published, the
ownership of the trade lifecycle moves to Order Exec.

v2 changes (from premium-diff v1):
  + strategy_id, instrument_id      multi-strategy attribution
  + score, score_breakdown          quality score from §4.8
  + net_pressure_at_signal          Strategy.md §4.7
  + decision_ts                     ms since epoch

v3 changes (Step 3 schema trim):
  + metrics_at_signal               free-form numeric metrics blob — each
    strategy defines its own metric names; consumed only for forensics /
    attribution, never for routing.
  - Dropped deprecated premium-diff legacy fields (diff_at_signal,
    sum_ce_at_signal, sum_pe_at_signal, delta_at_signal,
    delta_pcr_at_signal, strategy_version).
  - instrument_id / index relaxed from a hardcoded index Literal to str so
    stock-universe strategies can emit signals.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

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
    instrument_id: str = Field(
        ..., min_length=1, description="Instrument identifier; must match a vessel"
    )
    # Legacy alias for backward-compat with order_exec/dispatcher.py and
    # views that read `index`. Deprecated; use `instrument_id`.
    index: str = Field(..., min_length=1, description="Legacy alias of instrument_id")
    side: Literal["CE", "PE"]
    strike: int = Field(..., description="Selected strike")
    instrument_token: str = Field(..., description="Broker instrument key, e.g. 'NSE_FO|49520'")
    intent: SignalIntent
    qty_lots: int = Field(
        ..., gt=0, description="Number of lots; lot_size lives in instrument_config"
    )

    # New v2 fields
    score: float | None = Field(default=None, description="Quality score 0-10 (Strategy.md §4.8)")
    score_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Per-condition score breakdown"
    )
    net_pressure_at_signal: float | None = Field(
        default=None, description="Net pressure (cum_ce - cum_pe) at decision time"
    )
    decision_ts: int = Field(..., description="ms since epoch when the strategy decided")

    # Free-form per-strategy metrics at decision time. Each strategy defines
    # its own metric names (e.g. bid/ask imbalance publishes cum_ce_imbalance,
    # cum_pe_imbalance, net_pressure). Forensics/attribution only — order-exec
    # never routes on these.
    metrics_at_signal: dict[str, float] = Field(
        default_factory=dict, description="Strategy-defined numeric metrics at decision time"
    )

    # FULL strategy-defined state snapshot at decision time — any JSON-able
    # shape (nested dicts, per-strike breakdowns, gate results, labels...).
    # Flows into the position record and the closed-trade report so every
    # trade is forensically reconstructible. Strategies choose what to put
    # here; the infra never inspects it.
    strategy_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-defined JSON snapshot of decision state (free-form)",
    )

    ts: datetime = Field(..., description="Emission timestamp (UTC ISO-8601)")
