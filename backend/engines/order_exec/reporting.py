"""
engines.order_exec.reporting — Stage F (build + persist).

Builds a `ClosedPositionReport` and INSERTs into `trades_closed_positions`.
On failure the report is buffered to Redis (`orders:reports:pending`) so
Background can retry-flush; cleanup proceeds with WARN after 30s per
Strategy.md §10.5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import orjson
from loguru import logger

from engines.order_exec.entry import EntryResult
from engines.order_exec.exit_submit import ExitResult
from state.schemas.position import ExitReason, Position
from state.schemas.report import (
    ClosedPositionReport,
    Latencies,
    MarketSnapshot,
    OrderEventEntry,
    PnLBreakdown,
)

_INSERT_SQL = """
INSERT INTO trades_closed_positions (
    sig_id, index, mode, side, strike, instrument_token, qty,
    entry_ts, exit_ts, holding_seconds,
    entry_price, exit_price, pnl, pnl_pct,
    exit_reason, intent,
    signal_snapshot, pre_open_snapshot,
    market_snapshot_entry, market_snapshot_exit,
    exit_eval_history, trailing_history,
    order_events, latencies, pnl_breakdown,
    delta_pcr_at_entry, delta_pcr_at_exit,
    raw_broker_responses, strategy_version
) VALUES (
    $1, $2, $3, $4, $5, $6, $7,
    $8, $9, $10,
    $11, $12, $13, $14,
    $15, $16,
    $17, $18,
    $19, $20,
    $21, $22,
    $23, $24, $25,
    $26, $27,
    $28, $29
)
RETURNING id::text
"""


def _to_event_entries(events: list[dict[str, Any]]) -> list[OrderEventEntry]:
    out: list[OrderEventEntry] = []
    for e in events or []:
        ts_raw = e.get("ts")
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                ts = datetime.now(UTC)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = datetime.now(UTC)
        out.append(
            OrderEventEntry(
                ts=ts,
                event_type=str(e.get("event_type") or ""),
                order_id=str(e.get("order_id") or ""),
                qty=int(e["qty"]) if e.get("qty") is not None else None,
                price=float(e["price"]) if e.get("price") is not None else None,
                broker_status=str(e["broker_status"]) if e.get("broker_status") else None,
                note=str(e["note"]) if e.get("note") else None,
            )
        )
    return out


def build_report(
    *,
    position: Position,
    entry: EntryResult,
    exit_result: ExitResult,
    exit_reason: ExitReason,
    market_snapshot_entry: MarketSnapshot,
    market_snapshot_exit: MarketSnapshot,
    pre_open_snapshot: dict[str, Any],
    signal_snapshot: dict[str, Any],
    signal_to_submit_ms: int,
    exit_eval_history: list[dict[str, Any]] | None = None,
    trailing_history: list[dict[str, Any]] | None = None,
    delta_pcr_at_entry: float | None = None,
    delta_pcr_at_exit: float | None = None,
    raw_broker_responses: dict[str, Any] | None = None,
) -> ClosedPositionReport:
    qty = entry.filled_qty
    entry_price = float(entry.avg_fill_price)
    exit_price = float(exit_result.avg_fill_price)
    gross = (exit_price - entry_price) * qty
    # Charges and slippage are filled in by Background later if needed; we
    # store gross == net for paper mode (no fees), and refine on live.
    charges = 0.0
    slippage = 0.0
    net = gross - charges
    pnl_pct = (gross / (entry_price * qty)) * 100.0 if entry_price > 0 and qty > 0 else 0.0

    exit_ts = exit_result.order_events[-1].get("ts") if exit_result.order_events else None
    if isinstance(exit_ts, str):
        try:
            exit_ts_dt = datetime.fromisoformat(exit_ts)
        except ValueError:
            exit_ts_dt = datetime.now(UTC)
    elif isinstance(exit_ts, datetime):
        exit_ts_dt = exit_ts
    else:
        exit_ts_dt = datetime.now(UTC)

    holding_seconds = max(
        0,
        int((exit_ts_dt - position.entry_ts).total_seconds()),
    )

    return ClosedPositionReport(
        sig_id=position.sig_id,
        index=position.index,
        mode=position.mode,
        side=position.side,
        strike=position.strike,
        instrument_token=position.instrument_token,
        qty=qty,
        entry_ts=position.entry_ts,
        exit_ts=exit_ts_dt,
        holding_seconds=holding_seconds,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=round(net, 4),
        pnl_pct=round(pnl_pct, 4),
        exit_reason=exit_reason,
        intent=position.intent,
        signal_snapshot=signal_snapshot,
        pre_open_snapshot=pre_open_snapshot,
        market_snapshot_entry=market_snapshot_entry,
        market_snapshot_exit=market_snapshot_exit,
        exit_eval_history=exit_eval_history,
        trailing_history=trailing_history,
        order_events=_to_event_entries(entry.order_events + exit_result.order_events),
        latencies=Latencies(
            signal_to_submit_ms=int(signal_to_submit_ms),
            submit_to_ack_ms=int(entry.submit_to_ack_ms),
            ack_to_fill_ms=int(entry.ack_to_fill_ms),
            decision_to_exit_submit_ms=int(exit_result.decision_to_exit_submit_ms),
            exit_submit_to_fill_ms=int(exit_result.exit_submit_to_fill_ms),
        ),
        pnl_breakdown=PnLBreakdown(
            gross=round(gross, 4),
            charges=round(charges, 4),
            slippage=round(slippage, 4),
            net=round(net, 4),
        ),
        delta_pcr_at_entry=delta_pcr_at_entry,
        delta_pcr_at_exit=delta_pcr_at_exit,
        raw_broker_responses=raw_broker_responses,
        strategy_version=position.strategy_version,
    )


async def persist_report(pool: asyncpg.Pool, report: ClosedPositionReport) -> str:
    """INSERT report into `trades_closed_positions`. Returns the new row's UUID."""
    log = logger.bind(engine="order_exec", sig_id=report.sig_id)
    payload = report.model_dump(mode="json")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _INSERT_SQL,
                report.sig_id,
                report.index,
                report.mode,
                report.side,
                report.strike,
                report.instrument_token,
                report.qty,
                report.entry_ts,
                report.exit_ts,
                report.holding_seconds,
                report.entry_price,
                report.exit_price,
                report.pnl,
                report.pnl_pct,
                report.exit_reason.value
                if hasattr(report.exit_reason, "value")
                else str(report.exit_reason),
                report.intent,
                orjson.dumps(payload["signal_snapshot"]).decode(),
                orjson.dumps(payload["pre_open_snapshot"]).decode(),
                orjson.dumps(payload["market_snapshot_entry"]).decode(),
                orjson.dumps(payload["market_snapshot_exit"]).decode(),
                orjson.dumps(payload["exit_eval_history"]).decode() if payload.get("exit_eval_history") else None,
                orjson.dumps(payload["trailing_history"]).decode() if payload.get("trailing_history") else None,
                orjson.dumps(payload["order_events"]).decode(),
                orjson.dumps(payload["latencies"]).decode(),
                orjson.dumps(payload["pnl_breakdown"]).decode(),
                report.delta_pcr_at_entry,
                report.delta_pcr_at_exit,
                orjson.dumps(payload["raw_broker_responses"]).decode()
                if payload.get("raw_broker_responses")
                else None,
                report.strategy_version,
            )
        new_id = str(row["id"]) if row else ""
        log.info(f"persist_report: inserted trades_closed_positions id={new_id}")
        return new_id
    except Exception as e:
        log.exception(f"persist_report failed: {e!r}")
        raise
