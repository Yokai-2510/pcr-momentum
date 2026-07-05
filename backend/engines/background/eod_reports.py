"""
engines.background.eod_reports — per-strategy end-of-day report generation.

At market_close (scheduler event) the background engine aggregates today's
`trades_closed_positions` per strategy (attribution = strategy_version,
which carries the strategy_id) and UPSERTs one row per (date, strategy, mode)
into `strategy_daily_reports`. Cumulative "all till today" analysis is a SUM
over that table — served by /reports endpoints on the API gateway.

Every registered strategy gets a row even on a zero-trade day, so gaps in
the report history are meaningful (engine down) rather than ambiguous.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import orjson
import redis.asyncio as _redis_async
from loguru import logger

from state import registry

_IST = ZoneInfo("Asia/Kolkata")

_AGGREGATE_SQL = """
SELECT
    strategy_version AS strategy_id,
    mode,
    count(*)                                   AS trades,
    count(*) FILTER (WHERE pnl > 0)            AS wins,
    count(*) FILTER (WHERE pnl <= 0)           AS losses,
    coalesce(sum(pnl), 0)                      AS gross_pnl,
    coalesce(avg(pnl), 0)                      AS avg_pnl,
    coalesce(avg(pnl_pct), 0)                  AS avg_pnl_pct,
    coalesce(max(pnl), 0)                      AS best_trade_pnl,
    coalesce(min(pnl), 0)                      AS worst_trade_pnl,
    coalesce(avg(holding_seconds), 0)::int     AS avg_hold_sec
FROM trades_closed_positions
WHERE exit_ts >= $1::date AND exit_ts < ($1::date + interval '1 day')
GROUP BY strategy_version, mode
"""

_PER_INSTRUMENT_SQL = """
SELECT strategy_version AS strategy_id, mode, index AS instrument,
       count(*) AS trades, coalesce(sum(pnl), 0) AS pnl
FROM trades_closed_positions
WHERE exit_ts >= $1::date AND exit_ts < ($1::date + interval '1 day')
GROUP BY strategy_version, mode, index
"""

_EXIT_REASONS_SQL = """
SELECT strategy_version AS strategy_id, mode, exit_reason, count(*) AS n
FROM trades_closed_positions
WHERE exit_ts >= $1::date AND exit_ts < ($1::date + interval '1 day')
GROUP BY strategy_version, mode, exit_reason
"""

_UPSERT_SQL = """
INSERT INTO strategy_daily_reports (
    report_date, strategy_id, mode, trades, wins, losses, win_rate,
    gross_pnl, avg_pnl, avg_pnl_pct, best_trade_pnl, worst_trade_pnl,
    avg_hold_sec, per_instrument, exit_reasons, rejected_signals, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb,$16, now())
ON CONFLICT (report_date, strategy_id, mode) DO UPDATE SET
    trades = EXCLUDED.trades, wins = EXCLUDED.wins, losses = EXCLUDED.losses,
    win_rate = EXCLUDED.win_rate, gross_pnl = EXCLUDED.gross_pnl,
    avg_pnl = EXCLUDED.avg_pnl, avg_pnl_pct = EXCLUDED.avg_pnl_pct,
    best_trade_pnl = EXCLUDED.best_trade_pnl,
    worst_trade_pnl = EXCLUDED.worst_trade_pnl,
    avg_hold_sec = EXCLUDED.avg_hold_sec,
    per_instrument = EXCLUDED.per_instrument,
    exit_reasons = EXCLUDED.exit_reasons,
    rejected_signals = EXCLUDED.rejected_signals,
    updated_at = now()
"""

_REJECTED_SQL = """
SELECT count(*) AS n FROM trades_rejected_signals
WHERE ts >= $1::date AND ts < ($1::date + interval '1 day')
"""


async def generate_daily_reports(
    pool: asyncpg.Pool,
    redis_async: _redis_async.Redis,
    *,
    report_date: date | None = None,
) -> int:
    """Aggregate + upsert today's per-strategy report rows. Returns row count."""
    log = logger.bind(engine="background", task="eod_reports")
    rdate = report_date or datetime.now(_IST).date()

    async with pool.acquire() as conn:
        agg_rows = await conn.fetch(_AGGREGATE_SQL, rdate)
        per_instr_rows = await conn.fetch(_PER_INSTRUMENT_SQL, rdate)
        reason_rows = await conn.fetch(_EXIT_REASONS_SQL, rdate)
        rejected_row = await conn.fetchrow(_REJECTED_SQL, rdate)

        per_instrument: dict[tuple[str, str], dict[str, Any]] = {}
        for r in per_instr_rows:
            per_instrument.setdefault((r["strategy_id"], r["mode"]), {})[r["instrument"]] = {
                "trades": int(r["trades"]),
                "pnl": float(r["pnl"]),
            }
        exit_reasons: dict[tuple[str, str], dict[str, int]] = {}
        for r in reason_rows:
            exit_reasons.setdefault((r["strategy_id"], r["mode"]), {})[r["exit_reason"]] = int(
                r["n"]
            )
        rejected_total = int(rejected_row["n"]) if rejected_row else 0

        # Zero-trade rows for every registered strategy: an explicit "flat
        # day" beats an ambiguous gap in the history.
        seen: set[tuple[str, str]] = {(r["strategy_id"], r["mode"]) for r in agg_rows}
        registered = {sid for sid, _instr in await registry.list_vessels(redis_async)}

        written = 0
        for r in agg_rows:
            key = (r["strategy_id"], r["mode"])
            trades = int(r["trades"])
            win_rate = round(int(r["wins"]) / trades, 4) if trades else 0.0
            await conn.execute(
                _UPSERT_SQL,
                rdate,
                r["strategy_id"],
                r["mode"],
                trades,
                int(r["wins"]),
                int(r["losses"]),
                win_rate,
                float(r["gross_pnl"]),
                float(r["avg_pnl"]),
                float(r["avg_pnl_pct"]),
                float(r["best_trade_pnl"]),
                float(r["worst_trade_pnl"]),
                int(r["avg_hold_sec"]),
                orjson.dumps(per_instrument.get(key, {})).decode(),
                orjson.dumps(exit_reasons.get(key, {})).decode(),
                rejected_total,
            )
            written += 1

        for sid in sorted(registered):
            if any(s == sid for s, _m in seen):
                continue
            await conn.execute(
                _UPSERT_SQL,
                rdate,
                sid,
                "paper",
                0,
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                "{}",
                "{}",
                rejected_total,
            )
            written += 1

    log.info(f"eod_reports: {written} strategy report rows for {rdate}")
    return written
