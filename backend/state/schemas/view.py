"""Frontend view payload models — `ui:views:*` keys + WS push.

Per `docs/Schema.md` §1.6 + Frontend_Basics.md §3 (push-only contract).
View payloads are full replacements; the frontend never merges.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from state.schemas.health import HealthSummary
from state.schemas.pnl import DayPnL, PerIndexPnL


class StrategyView(BaseModel):
    """`ui:views:strategy:{index}` — built by the Strategy thread."""

    model_config = ConfigDict(extra="forbid")

    index: Literal["nifty50", "banknifty"]
    enabled: bool
    state: Literal["FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"]
    sum_ce: float
    sum_pe: float
    delta: float
    diffs: dict[str, float] = Field(default_factory=dict)
    cooldown_until_ts: int | None = None
    cooldown_reason: str | None = None
    entries_today: int = 0
    reversals_today: int = 0
    wins_today: int = 0
    last_decision_ts: int | None = None
    ts: datetime


class PositionView(BaseModel):
    """`ui:views:position:{index}` — built by Order Exec."""

    model_config = ConfigDict(extra="forbid")

    index: Literal["nifty50", "banknifty"]
    has_open: bool
    pos_id: str | None = None
    side: Literal["CE", "PE"] | None = None
    strike: int | None = None
    qty: int | None = None
    entry_price: float | None = None
    current_premium: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    sl_level: float | None = None
    target_level: float | None = None
    tsl_armed: bool = False
    tsl_level: float | None = None
    holding_seconds: int | None = None
    stage: str | None = None
    ts: datetime


class DeltaPCRView(BaseModel):
    """`ui:views:delta_pcr:{index}` — built by Background."""

    model_config = ConfigDict(extra="forbid")

    index: Literal["nifty50", "banknifty"]
    interval_pcr: float | None = None
    cumulative_pcr: float | None = None
    mode: Literal["1", "2", "3"] | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    ts: datetime


class PnLView(BaseModel):
    """`ui:views:pnl` — built by Background."""

    model_config = ConfigDict(extra="forbid")

    day: DayPnL
    per_index: dict[str, PerIndexPnL]
    ts: datetime


class CapitalView(BaseModel):
    """`ui:views:capital` — built by Background."""

    model_config = ConfigDict(extra="forbid")

    available: float
    used: float
    deployed: float
    kill_switch_active: bool = False
    ts: datetime


class HealthView(BaseModel):
    """`ui:views:health` — built by Health."""

    model_config = ConfigDict(extra="forbid")

    summary: HealthSummary
    ts: datetime


class ConfigsView(BaseModel):
    """`ui:views:configs` — built by Init + FastAPI on edit."""

    model_config = ConfigDict(extra="forbid")

    execution: dict[str, Any]
    session: dict[str, Any]
    risk: dict[str, Any]
    indexes: dict[str, dict[str, Any]]
    ts: datetime


class DashboardView(BaseModel):
    """`ui:views:dashboard` — top-level dashboard rollup built by Background."""

    model_config = ConfigDict(extra="forbid")

    trading_active: bool
    trading_disabled_reason: str
    mode: Literal["paper", "live"]
    auth_status: Literal["valid", "invalid", "missing", "unknown"]
    health_summary: Literal["OK", "DEGRADED", "DOWN", "UNKNOWN"]
    pnl: DayPnL
    open_positions_count: int
    ts: datetime
