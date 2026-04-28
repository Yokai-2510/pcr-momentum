"""Instrument-catalog models.

Per `docs/Schema.md` §1.3 + §5. The Init engine populates
`market_data:instruments:master` and per-index meta from these models.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OptionContract(BaseModel):
    """One row from the broker's option-contract feed."""

    model_config = ConfigDict(extra="forbid")

    instrument_token: str = Field(..., description="Broker key, e.g. 'NSE_FO|49520'")
    symbol: str = Field(..., description="Tradingsymbol (e.g. 'NIFTY24500CE')")
    underlying: str = Field(..., description="nifty50 / banknifty")
    expiry: date
    strike: int
    type: Literal["CE", "PE"]
    lot_size: int = Field(..., gt=0)
    tick_size: float = Field(..., gt=0)
    exchange: str = "NFO"


class IndexMeta(BaseModel):
    """`market_data:indexes:{index}:meta`."""

    model_config = ConfigDict(extra="forbid")

    index: Literal["nifty50", "banknifty"]
    strike_step: int = Field(..., gt=0)
    lot_size: int = Field(..., gt=0)
    exchange: str = "NFO"
    spot_token: str
    expiry: date
    prev_close: float
    atm_at_open: int
    ce_strikes: list[int]
    pe_strikes: list[int]
    ts: datetime | None = None


class OptionChainEntry(BaseModel):
    """One CE/PE leaf inside `market_data:indexes:{index}:option_chain`."""

    model_config = ConfigDict(extra="forbid")

    token: str
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: int = 0
    ask_qty: int = 0
    vol: int = 0
    oi: int = 0
    ts: int = Field(default=0, description="Epoch ms")
