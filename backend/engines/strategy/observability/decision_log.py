"""Per-tick decision telemetry (Strategy.md §11.1).

Every vessel evaluation emits ONE structured log record with:
  engine, strategy_id, instrument_id, ts, phase, state, metrics summary,
  action, reason, score, score_breakdown.

This goes to journalctl + the structured log file. It is the forensic record
of every decision — used to reconstruct any session post-hoc.

The function is sync + cheap (one logger call); safe to call from the runner
hot path. No I/O beyond the already-buffered log handler.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from engines.strategy.strategies.base import Action
from engines.strategy.strategies.bid_ask_imbalance.snapshot import Snapshot


def emit(
    strategy_id: str,
    instrument_id: str,
    snapshot: Snapshot,
    action: Action,
    *,
    state: str,
) -> None:
    metrics = action.metrics or {}
    summary: dict[str, Any] = {
        "atm": snapshot.atm,
        "spot": snapshot.spot,
        "net_pressure": metrics.get("net_pressure"),
        "net_pressure_label": metrics.get("net_pressure_label"),
        "cum_ce_imbalance": metrics.get("cum_ce_imbalance"),
        "cum_pe_imbalance": metrics.get("cum_pe_imbalance"),
    }
    logger.bind(
        engine="strategy",
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        phase="LIVE",
        state=state,
        action=action.kind.value,
        score=action.score,
    ).info(
        f"decision[{strategy_id}:{instrument_id}] {action.kind.value} "
        f"({action.reason}) np={summary['net_pressure']} "
        f"cum_ce={summary['cum_ce_imbalance']} cum_pe={summary['cum_pe_imbalance']} "
        f"score={action.score}"
    )
