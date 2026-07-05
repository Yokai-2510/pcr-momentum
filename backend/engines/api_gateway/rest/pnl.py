"""PnL endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import coerce_jsonb, decode, redis_get_json, row_to_dict
from state import keys as K

router = APIRouter(tags=["pnl"], dependencies=[Depends(require_admin)])


@router.get("/pnl/live")
async def pnl_live(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    view = await redis_get_json(redis, K.UI_VIEW_PNL, None)
    if isinstance(view, dict):
        return view
    day = await redis.hgetall(K.ORDERS_PNL_DAY)
    if not day:
        return {
            "realized_today": 0.0,
            "unrealized": 0.0,
            "total_today": 0.0,
            "trades_today": 0,
            "wins_today": 0,
            "win_rate": 0.0,
            "per_index": {},
        }
    decoded = {decode(k): decode(v) for k, v in day.items()}
    realized = float(decoded.get("realized") or 0)
    unrealized = float(decoded.get("unrealized") or 0)
    return {
        "realized_today": realized,
        "unrealized": unrealized,
        "total_today": realized + unrealized,
        "trades_today": int(decoded.get("trade_count") or 0),
        "win_rate": float(decoded.get("win_rate") or 0),
        "per_index": {},
    }


@router.get("/pnl/history")
async def pnl_history(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    index: str | None = None,
    granularity: Literal["1m", "5m", "15m", "1h", "1d"] = "1d",
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    if index is not None and index not in K.INDEXES:
        raise APIError(404, "INDEX_NOT_FOUND", f"Unknown index {index!r}")
    args: list[Any] = [from_date, to_date]
    where = ["ts::date >= $1", "ts::date <= $2"]
    if index is not None:
        args.append(index)
        where.append(f"index = ${len(args)}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT ts, index, realized, unrealized, open_count, day_trades
            FROM metrics_pnl_history
            WHERE {" AND ".join(where)}
            ORDER BY ts ASC
            """,
            *args,
        )
    return {"granularity": granularity, "series": [row_to_dict(row) for row in rows]}


@router.get("/reports/daily")
async def reports_daily(
    strategy_id: str | None = None,
    days: int = 30,
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    """Per-strategy EOD report rows (most recent first)."""
    days = max(1, min(days, 365))
    sql = (
        "SELECT report_date, strategy_id, mode, trades, wins, losses, win_rate, "
        "gross_pnl, avg_pnl, avg_pnl_pct, best_trade_pnl, worst_trade_pnl, "
        "avg_hold_sec, per_instrument, exit_reasons "
        "FROM strategy_daily_reports "
        + ("WHERE strategy_id = $2 " if strategy_id else "")
        + "ORDER BY report_date DESC LIMIT $1"
    )
    async with pool.acquire() as conn:
        rows = (
            await conn.fetch(sql, days, strategy_id) if strategy_id else await conn.fetch(sql, days)
        )
    return {
        "reports": [
            {
                **dict(r),
                "report_date": r["report_date"].isoformat(),
                "per_instrument": coerce_jsonb(r["per_instrument"]),
                "exit_reasons": coerce_jsonb(r["exit_reasons"]),
            }
            for r in rows
        ]
    }


@router.get("/reports/summary")
async def reports_summary(pool: Any = Depends(get_postgres)) -> dict[str, Any]:
    """Cumulative all-time per-strategy analysis (SUM over the daily table)."""
    sql = (
        "SELECT strategy_id, mode, count(*) AS days, sum(trades) AS trades, "
        "sum(wins) AS wins, sum(losses) AS losses, sum(gross_pnl) AS gross_pnl, "
        "max(best_trade_pnl) AS best_trade_pnl, min(worst_trade_pnl) AS worst_trade_pnl, "
        "min(report_date) AS first_date, max(report_date) AS last_date "
        "FROM strategy_daily_reports GROUP BY strategy_id, mode ORDER BY strategy_id"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    out = []
    for r in rows:
        trades = int(r["trades"] or 0)
        out.append(
            {
                "strategy_id": r["strategy_id"],
                "mode": r["mode"],
                "days": int(r["days"]),
                "trades": trades,
                "wins": int(r["wins"] or 0),
                "losses": int(r["losses"] or 0),
                "win_rate": round(int(r["wins"] or 0) / trades, 4) if trades else 0.0,
                "gross_pnl": float(r["gross_pnl"] or 0),
                "best_trade_pnl": float(r["best_trade_pnl"] or 0),
                "worst_trade_pnl": float(r["worst_trade_pnl"] or 0),
                "first_date": r["first_date"].isoformat() if r["first_date"] else None,
                "last_date": r["last_date"].isoformat() if r["last_date"] else None,
            }
        )
    return {"strategies": out}
