"""`Signal` — Strategy Engine emits, Order Exec consumes.

Per `docs/Schema.md` §5 and `docs/Strategy.md` §10. A signal is the
commitment to enter a position; once published, the Strategy thread for
that index transitions to `IN_CE` / `IN_PE` and ownership of the
trade-lifecycle moves to Order Exec.
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


class Signal(BaseModel):
    """A trade intent published by Strategy → consumed by Order Exec."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sig_id: str = Field(..., description="Monotonic per-day id, e.g. 'nifty50_1714290330123'")
    index: Literal["nifty50", "banknifty"]
    side: Literal["CE", "PE"]
    strike: int = Field(..., description="Selected strike (highest-Diff in side)")
    instrument_token: str = Field(..., description="Broker instrument key, e.g. 'NSE_FO|49520'")
    intent: SignalIntent
    qty_lots: int = Field(..., gt=0, description="Number of lots; lot_size lives in IndexConfig")
    diff_at_signal: float = Field(..., description="Strike-Diff value at signal time (rupees)")
    sum_ce_at_signal: float
    sum_pe_at_signal: float
    delta_at_signal: float = Field(..., description="SUM_PE - SUM_CE at signal time")
    delta_pcr_at_signal: float | None = Field(
        default=None, description="Latest ΔPCR cumulative; None if not yet computed"
    )
    strategy_version: str = Field(..., description="Git short-SHA of strategy module")
    ts: datetime = Field(..., description="Emission timestamp (UTC ISO-8601)")
