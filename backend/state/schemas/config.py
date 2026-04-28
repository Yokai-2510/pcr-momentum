"""Runtime configuration models — mirror `strategy:configs:*` Redis JSON.

Per `docs/Schema.md` §4 + Strategy.md §14. These are the typed forms of
the JSON values stored in Postgres `config_settings.value` and mirrored
into Redis at boot by Init.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionConfig(BaseModel):
    """`strategy:configs:execution` (Schema.md §4.1)."""

    model_config = ConfigDict(extra="forbid")

    buffer_inr: float = Field(..., ge=0)
    eod_buffer_inr: float = Field(..., ge=0)
    spread_skip_pct: float = Field(..., ge=0, le=1)
    drift_threshold_inr: float = Field(..., ge=0)
    chase_ceiling_inr: float = Field(..., ge=0)
    open_timeout_sec: int = Field(..., gt=0)
    partial_grace_sec: int = Field(..., ge=0)
    max_retries: int = Field(..., ge=0)
    worker_pool_size: int = Field(..., gt=0)
    liquidity_exit_suppress_after: str = Field(
        ..., description="HH:MM after which liquidity exits are suppressed"
    )


class SessionConfig(BaseModel):
    """`strategy:configs:session` (Schema.md §4.2). All times are HH:MM IST."""

    model_config = ConfigDict(extra="forbid")

    market_open: str = "09:15"
    pre_open_snapshot: str = "09:14:50"
    ws_subscribe_at: str = "09:14:00"
    delta_pcr_first_compute: str = "09:18"
    delta_pcr_interval_minutes: int = Field(3, gt=0)
    entry_freeze: str = "15:10"
    eod_squareoff: str = "15:15"
    market_close: str = "15:30"
    graceful_shutdown: str = "15:45"
    instrument_refresh: str = "05:30"


class RiskConfig(BaseModel):
    """`strategy:configs:risk` (Schema.md §4.3)."""

    model_config = ConfigDict(extra="forbid")

    daily_loss_circuit_pct: float = Field(..., gt=0, lt=1)
    max_concurrent_positions: int = Field(..., gt=0)
    trading_capital_inr: float = Field(..., gt=0)


class IndexConfig(BaseModel):
    """`strategy:configs:indexes:{index}` — full IndexConfig per Strategy.md §14."""

    model_config = ConfigDict(extra="forbid")

    # Identity
    index: Literal["nifty50", "banknifty"]
    strike_step: int = Field(..., gt=0)
    lot_size: int = Field(..., gt=0)
    exchange: str = "NFO"

    # Strike basket
    pre_open_subscribe_window: int = Field(..., gt=0, description="ATM ± N strikes subscribed")
    trading_basket_range: int = Field(..., gt=0, description="ATM ± N strikes used for entry")

    # Premium-diff thresholds
    reversal_threshold_inr: float = Field(..., gt=0)
    entry_dominance_threshold_inr: float = Field(..., gt=0)

    # Cooldowns
    post_sl_cooldown_sec: int = Field(..., ge=0)
    post_reversal_cooldown_sec: int = Field(..., ge=0)

    # Daily caps
    max_entries_per_day: int = Field(..., gt=0)
    max_reversals_per_day: int = Field(..., ge=0)

    # Sizing
    qty_lots: int = Field(..., gt=0, description="Lots placed per entry")

    # Exit profile defaults (resolved into Position.ExitProfile at entry time)
    sl_pct: float = Field(..., gt=0, lt=1)
    target_pct: float = Field(..., gt=0)
    tsl_arm_pct: float = Field(..., gt=0)
    tsl_trail_pct: float = Field(..., gt=0, lt=1)
    max_hold_sec: int = Field(..., gt=0)

    # ΔPCR overlay
    delta_pcr_required_for_entry: bool = False
