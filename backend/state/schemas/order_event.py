"""`OrderEvent` — broker → Background → Order Exec / FastAPI.

Per `docs/Schema.md` §5. Every broker-side state change (order placement,
modification, cancellation, fill) becomes one `OrderEvent` published on
`orders:stream:order_events`. Background's portfolio-WS thread is the
sole writer; consumers are Order Exec (advance position state machine)
and FastAPI (push to frontend).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderEventType(StrEnum):
    """All broker-side state transitions we care about."""

    SUBMIT = "SUBMIT"
    ACK = "ACK"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    FILL = "FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    REJECT = "REJECT"


class OrderEvent(BaseModel):
    """One broker-side order lifecycle event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: OrderEventType
    order_id: str = Field(..., description="Broker order ID")
    position_id: str | None = Field(
        default=None, description="Local pos_id this order belongs to"
    )
    sig_id: str | None = None
    index: str | None = Field(default=None, description="nifty50 / banknifty")
    instrument_token: str | None = None
    side: str | None = Field(default=None, description="CE / PE")
    qty: int | None = None
    filled_qty: int | None = None
    price: float | None = Field(default=None, description="Limit price for placement")
    avg_price: float | None = Field(default=None, description="Avg fill price (post-fill)")
    broker_status: str | None = Field(default=None, description="Raw broker status code/string")
    reject_reason: str | None = None
    ts: datetime = Field(..., description="Event timestamp (UTC ISO-8601)")
    internal_latency_ms: int | None = Field(
        default=None, description="Internal processing latency (e.g. submit → ack)"
    )
    raw: dict[str, Any] | None = Field(
        default=None, description="Raw broker payload (audit only)"
    )
