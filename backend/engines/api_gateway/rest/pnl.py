"""PnL endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import decode, redis_get_json, row_to_dict
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
            WHERE {' AND '.join(where)}
            ORDER BY ts ASC
            """,
            *args,
        )
    return {"granularity": granularity, "series": [row_to_dict(row) for row in rows]}
