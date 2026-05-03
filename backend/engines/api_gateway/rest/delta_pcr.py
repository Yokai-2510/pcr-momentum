"""Delta-PCR endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import json_loads_maybe, redis_get_json, row_to_dict
from state import keys as K

router = APIRouter(tags=["delta-pcr"], dependencies=[Depends(require_admin)])


class DeltaPCRModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: int = Field(..., ge=1, le=3)


def _validate_index(index: str) -> str:
    if index not in K.INDEXES:
        raise APIError(404, "INDEX_NOT_FOUND", f"Unknown index {index!r}")
    return index


@router.get("/delta_pcr/{index}/live")
async def delta_pcr_live(index: str, redis: Any = Depends(get_redis)) -> dict[str, Any]:
    index = _validate_index(index)
    view = await redis_get_json(redis, K.ui_view_delta_pcr(index), None)
    if isinstance(view, dict):
        return view
    pipe = redis.pipeline(transaction=False)
    pipe.get(K.delta_pcr_interval(index))
    pipe.get(K.delta_pcr_cumulative(index))
    pipe.lrange(K.delta_pcr_history(index), 0, 19)
    pipe.get(K.delta_pcr_mode(index))
    interval, cumulative, history_raw, mode = await pipe.execute()
    return {
        "index": index,
        "interval": json_loads_maybe(interval, None),
        "cumulative": json_loads_maybe(cumulative, None),
        "history": [json_loads_maybe(v, {}) for v in (history_raw or [])],
        "interpretation": "UNKNOWN",
        "mismatch_flag": False,
        "mode": int(mode or 1),
    }


@router.get("/delta_pcr/{index}/history")
async def delta_pcr_history(
    index: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    index = _validate_index(index)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM metrics_delta_pcr_history
            WHERE index = $1 AND ts::date >= $2 AND ts::date <= $3
            ORDER BY ts ASC
            """,
            index,
            from_date,
            to_date,
        )
    return {"items": [row_to_dict(row) for row in rows]}


@router.put("/delta_pcr/{index}/mode")
async def put_delta_pcr_mode(
    index: str,
    payload: DeltaPCRModeRequest,
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    index = _validate_index(index)
    await redis.set(K.delta_pcr_mode(index), str(payload.mode))
    await redis.publish(
        K.SYSTEM_PUB_SYSTEM_EVENT,
        orjson.dumps({"event": "delta_pcr_mode", "index": index, "mode": payload.mode}),
    )
    return {"ok": True, "index": index, "mode": payload.mode}

