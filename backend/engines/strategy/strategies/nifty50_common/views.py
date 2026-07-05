"""Shared universe-view contract for NIFTY-50 stock strategies.

Both universe strategies build the SAME snapshot shape from the runner's
MarketView; it lives here so the strategies stay fully independent of each
other (shared LIBRARY code only — never cross-strategy imports).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class UniverseView:
    """Per-tick universe snapshot: spot map + lazy per-symbol chain access."""

    instrument_id: str
    now_ms: int
    spot: dict[str, dict[str, Any]]  # SYMBOL -> {ltp, prev_close, change_pct, volume, ts}
    symbols: dict[str, dict[str, Any]]  # universe meta symbols
    read_chain: Callable[[str], dict[str, Any]]


def empty_chain(_symbol: str) -> dict[str, Any]:
    return {}
