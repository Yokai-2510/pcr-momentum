"""vwap_band_v1 — VWAP Band. Ported from pcr_analytics; fully tick-driven."""

from __future__ import annotations

from typing import Any

from engines.strategy.strategies.pcr_common import indicators
from engines.strategy.strategies.pcr_common.engine import (
    IndexView,
    PcrMemory,
    PcrStrategyBase,
)

STRATEGY_ID = "vwap_band_v1"
STRATEGY_NAME = "VWAP Band"
STRATEGY_DESCRIPTION = "Session-anchored VWAP (spot x incremental ATM-band option volume, anchored 09:15): spot above VWAP +0.05% -> BUY CE; below -0.05% -> BUY PE. Fresh crossovers only; a dip back inside the band never resets the side. Own exit stack."


def _step_of(view: IndexView) -> int:
    strikes = sorted(view.ce) or sorted(view.pe)
    if len(strikes) >= 2:
        return min(b - a for a, b in zip(strikes, strikes[1:], strict=False) if b > a)
    return 50


class VwapBandStrategy(PcrStrategyBase):
    """Signal state machine for VWAP Band; entries/exits run in PcrStrategyBase
    against THIS strategy's own config blob."""

    def compute_signal(
        self, view: IndexView, memory: PcrMemory, cfg: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        band = int((cfg.get("indicator") or {}).get("band_strikes", 5))
        memory.vwap.band_pct = float((cfg.get("indicator") or {}).get("band_pct", 0.0005))
        step = _step_of(view)
        totals = indicators.band_totals(view.ce, view.pe, view.atm, step, band)
        signal = memory.vwap.update(view.spot or 0.0, totals["ce_vol"], totals["pe_vol"])
        return signal, {
            "vwap": round(memory.vwap.vwap, 2),
            "spot": view.spot,
            "band_pct": memory.vwap.band_pct,
        }
