"""Position, history, report, and manual-exit endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import (
    coerce_jsonb,
    redis_hgetall_decoded,
    redis_smembers_decoded,
    row_to_dict,
)
from state import keys as K

router = APIRouter(tags=["positions"], dependencies=[Depends(require_admin)])


class ManualExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3)


def _parse_position_hash(raw: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value == "":
            continue
        try:
            out[key] = orjson.loads(value)
        except Exception:
            out[key] = value
    return out


@router.get("/positions/open")
async def positions_open(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    ids = sorted(await redis_smembers_decoded(redis, K.ORDERS_POSITIONS_OPEN))
    items: list[dict[str, Any]] = []
    for pos_id in ids:
        raw = await redis_hgetall_decoded(redis, K.orders_position(pos_id))
        if not raw:
            continue
        item = _parse_position_hash(raw)
        status = await redis_hgetall_decoded(redis, K.orders_status(pos_id))
        if status:
            item["status_stage"] = status.get("stage")
        items.append(item)
    return {"items": items}


@router.get("/positions/closed_today")
async def positions_closed_today(pool: Any = Depends(get_postgres)) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM trades_closed_positions
            WHERE exit_ts::date = CURRENT_DATE
            ORDER BY exit_ts DESC
            """
        )
    return {"items": [row_to_dict(row) for row in rows]}


@router.get("/positions/history")
async def positions_history(
    index: str | None = None,
    mode: Literal["paper", "live"] | None = None,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Literal["entry_ts_asc", "entry_ts_desc", "pnl_desc", "pnl_asc"] = "entry_ts_desc",
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    if index is not None and index not in K.INDEXES:
        raise APIError(404, "INDEX_NOT_FOUND", f"Unknown index {index!r}")
    order_by = {
        "entry_ts_asc": "entry_ts ASC",
        "entry_ts_desc": "entry_ts DESC",
        "pnl_desc": "pnl DESC",
        "pnl_asc": "pnl ASC",
    }[sort]
    conditions = ["entry_ts::date >= $1", "entry_ts::date <= $2"]
    args: list[Any] = [from_date, to_date]
    if index is not None:
        args.append(index)
        conditions.append(f"index = ${len(args)}")
    if mode is not None:
        args.append(mode)
        conditions.append(f"mode = ${len(args)}")
    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM trades_closed_positions WHERE {where}", *args)
        rows = await conn.fetch(
            f"""
            SELECT * FROM trades_closed_positions
            WHERE {where}
            ORDER BY {order_by}
            LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
            """,
            *args,
            page_size,
            offset,
        )
    total_int = int(total or 0)
    return {
        "items": [row_to_dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total_int,
        "total_pages": (total_int + page_size - 1) // page_size,
    }


@router.get("/reports/{position_id}")
async def report(position_id: str, pool: Any = Depends(get_postgres)) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM trades_closed_positions WHERE id::text = $1 OR sig_id = $1",
            position_id,
        )
    if row is None:
        raise APIError(404, "POSITION_NOT_FOUND", "Position report not found")
    return {k: coerce_jsonb(v) for k, v in row_to_dict(row).items()}


@router.post("/commands/manual_exit/{position_id}")
async def manual_exit(
    position_id: str,
    payload: ManualExitRequest,
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    raw = await redis_hgetall_decoded(redis, K.orders_position(position_id))
    if not raw:
        raise APIError(404, "POSITION_NOT_FOUND", "Open position not found")
    await redis.xadd(
        K.ORDERS_STREAM_MANUAL_EXIT,
        {"position_id": position_id, "reason": payload.reason},
        maxlen=1000,
        approximate=True,
    )
    return {"ok": True, "queued": True, "position_id": position_id}
