"""ΔPCR (delta-PCR) models — Background ΔPCR thread output.

Per `docs/Schema.md` §1.4 (`strategy:{index}:delta_pcr:*`) + §5. ΔPCR is
computed per-index every `delta_pcr_interval_minutes` (default 3 min).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeltaPCRInterval(BaseModel):
    """One interval's worth of ΔPCR (`strategy:{index}:delta_pcr:interval`)."""

    model_config = ConfigDict(extra="forbid")

    interval_pcr: float = Field(..., description="d_put / d_call for this interval")
    total_d_put: int = Field(..., description="Sum of (current - last) across PE strikes")
    total_d_call: int = Field(..., description="Sum of (current - last) across CE strikes")
    atm: int = Field(..., description="ATM at compute time")
    ts: datetime


class DeltaPCRCumulative(BaseModel):
    """Cumulative ΔPCR since session open (`strategy:{index}:delta_pcr:cumulative`)."""

    model_config = ConfigDict(extra="forbid")

    cumulative_pcr: float
    cumulative_d_put: int
    cumulative_d_call: int
    ts: datetime


class DeltaPCRHistoryEntry(BaseModel):
    """One element of the bounded `delta_pcr:history` LIST."""

    model_config = ConfigDict(extra="forbid")

    interval: DeltaPCRInterval
    cumulative: DeltaPCRCumulative
