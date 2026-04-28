"""Health-engine output models.

Per `docs/Schema.md` §1.1 + §5. `system:health:summary`,
`system:health:engines`, `system:health:dependencies` are the on-Redis
payloads whose typed forms are these models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Status string conventions used across health outputs.
HealthStatus = Literal["OK", "DEGRADED", "DOWN", "UNKNOWN"]


class EngineStatus(BaseModel):
    """One row of `system:health:engines`."""

    model_config = ConfigDict(extra="forbid")

    alive: bool
    last_hb_ts: datetime | None = Field(
        default=None, description="Last heartbeat seen by Health"
    )
    restart_count: int = 0
    note: str | None = None


class DependencyStatus(BaseModel):
    """One row of `system:health:dependencies`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="redis | postgres | broker_market_ws | ...")
    status: HealthStatus
    last_probe_ts: datetime | None = None
    detail: str | None = None
    latency_ms: int | None = None


class HealthSummary(BaseModel):
    """Top-level health rollup (`system:health:summary` + view payload)."""

    model_config = ConfigDict(extra="forbid")

    summary: HealthStatus
    engines: dict[str, EngineStatus] = Field(default_factory=dict)
    dependencies: dict[str, DependencyStatus] = Field(default_factory=dict)
    auth: Literal["valid", "invalid", "missing", "unknown"] = "unknown"
    ts: datetime
