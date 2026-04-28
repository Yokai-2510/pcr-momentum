"""`PnL` snapshots — Background → Redis (`orders:pnl:*`) + Postgres rollups.

Per `docs/Schema.md` §1.5 + §5.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PerIndexPnL(BaseModel):
    """Per-index running totals, refreshed each PnL tick (~1Hz)."""

    model_config = ConfigDict(extra="forbid")

    realized: float = 0.0
    unrealized: float = 0.0
    trades_count: int = 0
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_pnl_pct: float = 0.0


class DayPnL(BaseModel):
    """Whole-day rollup; `orders:pnl:day` (HASH)."""

    model_config = ConfigDict(extra="forbid")

    realized: float = 0.0
    unrealized: float = 0.0
    trade_count: int = 0
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    day_pnl_pct_of_capital: float = 0.0
    ts: datetime | None = Field(default=None, description="When this snapshot was built")
