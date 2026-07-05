"""volume_diff_v1 — Volume Diff. Ported from pcr_analytics; fully tick-driven."""

from __future__ import annotations

from typing import Any

from engines.strategy.strategies.pcr_common import indicators
from engines.strategy.strategies.pcr_common.engine import (
    IndexView,
    PcrMemory,
    PcrStrategyBase,
)

STRATEGY_ID = "volume_diff_v1"
STRATEGY_NAME = "Volume Diff"
STRATEGY_DESCRIPTION = "Tick-driven ATM-band volume difference: VOL DIFF = PE volume (ATM..ATM-5 puts) - CE volume (ATM..ATM+5 calls). Negative (CE heavier) -> BUY CE; positive -> BUY PE. Emits on the first directional reading and on each sign flip. Own exit stack."


def _step_of(view: IndexView) -> int:
    strikes = sorted(view.ce) or sorted(view.pe)
    if len(strikes) >= 2:
        return min(b - a for a, b in zip(strikes, strikes[1:], strict=False) if b > a)
    return 50


class VolumeDiffStrategy(PcrStrategyBase):
    """Signal state machine for Volume Diff; entries/exits run in PcrStrategyBase
    against THIS strategy's own config blob."""

    def compute_signal(
        self, view: IndexView, memory: PcrMemory, cfg: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        band = int((cfg.get("indicator") or {}).get("band_strikes", 5))
        step = _step_of(view)
        totals = indicators.band_totals(view.ce, view.pe, view.atm, step, band)
        vol_diff = totals["pe_vol"] - totals["ce_vol"]
        state = "BUY" if vol_diff < 0 else "SELL" if vol_diff > 0 else None
        signal = None
        if state is not None and state != memory.vol_prev:
            memory.vol_prev = state
            signal = state
        return signal, {
            "vol_difference": vol_diff,
            "ce_vol": totals["ce_vol"],
            "pe_vol": totals["pe_vol"],
        }
