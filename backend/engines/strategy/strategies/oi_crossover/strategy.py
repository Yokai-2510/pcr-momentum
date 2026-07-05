"""oi_crossover_v1 — OI Crossover. Ported from pcr_analytics; fully tick-driven."""

from __future__ import annotations

from typing import Any

from engines.strategy.strategies.pcr_common import indicators
from engines.strategy.strategies.pcr_common.engine import (
    IndexView,
    PcrMemory,
    PcrStrategyBase,
)

STRATEGY_ID = "oi_crossover_v1"
STRATEGY_NAME = "OI Crossover"
STRATEGY_DESCRIPTION = "Tick-driven OI difference crossover: diff = cumulative PE OI change minus CE OI change over the ATM band (vs first tick of the session). diff flips positive -> BUY CE; flips negative -> SELL -> BUY PE. Own exit stack: counter-crossover, SL, target, TSL, peak-trail, time, EOD."


def _step_of(view: IndexView) -> int:
    strikes = sorted(view.ce) or sorted(view.pe)
    if len(strikes) >= 2:
        return min(b - a for a, b in zip(strikes, strikes[1:], strict=False) if b > a)
    return 50


class OiCrossoverStrategy(PcrStrategyBase):
    """Signal state machine for OI Crossover; entries/exits run in PcrStrategyBase
    against THIS strategy's own config blob."""

    def compute_signal(
        self, view: IndexView, memory: PcrMemory, cfg: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        band = int((cfg.get("indicator") or {}).get("band_strikes", 5))
        step = _step_of(view)
        totals = indicators.band_totals(view.ce, view.pe, view.atm, step, band)
        signal, diff = memory.oi.update(totals["ce_oi"], totals["pe_oi"])
        return signal, {
            "oi_difference": diff,
            "ce_oi": totals["ce_oi"],
            "pe_oi": totals["pe_oi"],
        }
