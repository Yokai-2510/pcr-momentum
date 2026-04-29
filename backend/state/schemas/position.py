"""`Position` — Order Exec position state machine.

Per `docs/Schema.md` §1.7 (Position HASH) + §5. Single-writer rule:
the Order Exec engine is the only writer of `orders:positions:{pos_id}`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExitReason(StrEnum):
    """Why a position was closed (Schema.md §2.4)."""

    HARD_SL = "HARD_SL"
    HARD_TARGET = "HARD_TARGET"
    TRAILING_SL = "TRAILING_SL"
    REVERSAL_FLIP = "REVERSAL_FLIP"
    TIME_EXIT = "TIME_EXIT"
    EOD = "EOD"
    LIQUIDITY = "LIQUIDITY"
    DAILY_LOSS_CIRCUIT = "DAILY_LOSS_CIRCUIT"
    MANUAL = "MANUAL"


class PositionStage(StrEnum):
    """Order-Exec progress through a single trade (Schema.md §1.5)."""

    GATE_PREENTRY = "GATE_PREENTRY"
    ENTRY_SUBMITTING = "ENTRY_SUBMITTING"
    ENTRY_OPEN = "ENTRY_OPEN"
    ENTRY_FILLED = "ENTRY_FILLED"
    EXIT_EVAL = "EXIT_EVAL"
    EXIT_SUBMITTING = "EXIT_SUBMITTING"
    EXIT_OPEN = "EXIT_OPEN"
    EXIT_FILLED = "EXIT_FILLED"
    REPORTING = "REPORTING"
    CLEANUP = "CLEANUP"
    DONE = "DONE"
    ABORTED = "ABORTED"


class ExitProfile(BaseModel):
    """Exit thresholds resolved from IndexConfig at entry time."""

    model_config = ConfigDict(extra="forbid")

    sl_pct: float = Field(..., description="Hard stop-loss as fraction of entry premium")
    target_pct: float = Field(..., description="Hard take-profit as fraction of entry premium")
    tsl_arm_pct: float = Field(..., description="Premium-rise % at which trailing SL arms")
    tsl_trail_pct: float = Field(..., description="Trailing distance once armed (fraction of peak)")
    max_hold_sec: int = Field(..., gt=0, description="Time-exit ceiling in seconds")


class Position(BaseModel):
    """The full Position HASH per Schema.md §1.7."""

    model_config = ConfigDict(extra="forbid")

    pos_id: str
    sig_id: str
    index: Literal["nifty50", "banknifty"]
    side: Literal["CE", "PE"]
    strike: int
    instrument_token: str
    qty: int = Field(..., gt=0)
    entry_order_id: str
    exit_order_id: str | None = None
    entry_price: float = Field(..., description="Avg fill price")
    entry_ts: datetime
    exit_price: float | None = None
    exit_ts: datetime | None = None
    mode: Literal["paper", "live"]
    intent: Literal["FRESH_ENTRY", "REVERSAL_FLIP"]
    sl_level: float = Field(..., description="Absolute premium price")
    target_level: float
    tsl_armed: bool = False
    tsl_arm_pct: float
    tsl_trail_pct: float
    tsl_level: float | None = None
    peak_premium: float
    current_premium: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    holding_seconds: int = 0
    exit_profile: ExitProfile
    sum_ce_at_entry: float
    sum_pe_at_entry: float
    delta_pcr_at_entry: float | None = None
    strategy_version: str
