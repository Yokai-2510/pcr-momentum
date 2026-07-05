"""ltp_strength_v1 — LTP Strength. Ported from pcr_analytics; fully tick-driven."""

from __future__ import annotations

from typing import Any

from engines.strategy.strategies.pcr_common import indicators
from engines.strategy.strategies.pcr_common.engine import (
    IndexView,
    PcrMemory,
    PcrStrategyBase,
)

STRATEGY_ID = "ltp_strength_v1"
STRATEGY_NAME = "LTP Strength"
STRATEGY_DESCRIPTION = "LTP-based option strength (Dr. Vijay STEP 11-13): session CE_SUM / PE_SUM over ATM+3 ITM strikes, directional + ~5-min rolling strength, confirmed by spot vs session VWAP. Strict 5-condition BUY CE / BUY PE; regime flips only vs the last traded side. Own exit stack."


def _step_of(view: IndexView) -> int:
    strikes = sorted(view.ce) or sorted(view.pe)
    if len(strikes) >= 2:
        return min(b - a for a, b in zip(strikes, strikes[1:], strict=False) if b > a)
    return 50


class LtpStrengthStrategy(PcrStrategyBase):
    """Signal state machine for LTP Strength; entries/exits run in PcrStrategyBase
    against THIS strategy's own config blob."""

    def compute_signal(
        self, view: IndexView, memory: PcrMemory, cfg: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        band = int((cfg.get("indicator") or {}).get("band_strikes", 5))
        step = _step_of(view)
        totals = indicators.band_totals(view.ce, view.pe, view.atm, step, band)
        memory.vwap.update(view.spot or 0.0, totals["ce_vol"], totals["pe_vol"])
        memory.ltp.rolling_ms = int((cfg.get("indicator") or {}).get("rolling_minutes", 5)) * 60_000
        signal, metrics = memory.ltp.update(
            view.now_ms,
            view.atm,
            step,
            view.ce,
            view.pe,
            view.spot or 0.0,
            memory.vwap.vwap,
        )
        return signal, dict(metrics)
