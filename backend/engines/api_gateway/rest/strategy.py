"""Strategy status and control endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from engines.api_gateway.deps import get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import decode, now_iso, redis_smembers_decoded
from state import keys as K

router = APIRouter(tags=["strategy"], dependencies=[Depends(require_admin)])


class ReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3)
    reset_daily_loss_circuit: bool = True


def _validate_index(index: str) -> str:
    if index not in K.INDEXES:
        raise APIError(404, "INDEX_NOT_FOUND", f"Unknown index {index!r}")
    return index


@router.get("/strategy/status")
async def strategy_status(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    pipe = redis.pipeline(transaction=False)
    pipe.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)
    pipe.get(K.SYSTEM_FLAGS_MODE)
    pipe.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)
    for index in K.INDEXES:
        pipe.get(K.strategy_enabled(index))
        pipe.get(K.strategy_state(index))
        pipe.get(K.strategy_current_position_id(index))
    values = await pipe.execute()
    out = {
        "system": {
            "trading_active": decode(values[0]).lower() == "true",
            "mode": decode(values[1]) or "paper",
            "daily_loss_circuit_triggered": decode(values[2]).lower() == "true",
        },
        "indexes": {},
    }
    offset = 3
    for index in K.INDEXES:
        enabled, state, pos_id = values[offset : offset + 3]
        offset += 3
        out["indexes"][index] = {
            "enabled": decode(enabled).lower() == "true",
            "state": decode(state) or "FLAT",
            "current_position_id": decode(pos_id) or None,
        }
    return out


@router.post("/commands/halt_index/{index}")
async def halt_index(index: str, redis: Any = Depends(get_redis)) -> dict[str, Any]:
    index = _validate_index(index)
    await redis.set(K.strategy_enabled(index), "false")
    await redis.publish(K.SYSTEM_PUB_SYSTEM_EVENT, f'{{"event":"halt_index","index":"{index}"}}')
    return {"ok": True, "index": index, "enabled": False}


@router.post("/commands/resume_index/{index}")
async def resume_index(index: str, redis: Any = Depends(get_redis)) -> dict[str, Any]:
    index = _validate_index(index)
    await redis.set(K.strategy_enabled(index), "true")
    await redis.publish(K.SYSTEM_PUB_SYSTEM_EVENT, f'{{"event":"resume_index","index":"{index}"}}')
    return {"ok": True, "index": index, "enabled": True}


@router.post("/commands/global_kill")
async def global_kill(
    payload: ReasonRequest,
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    open_positions = await redis_smembers_decoded(redis, K.ORDERS_POSITIONS_OPEN)
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "manual_kill")
    pipe.xadd(
        K.SYSTEM_STREAM_CONTROL,
        {"event": "global_kill", "reason": payload.reason},
        maxlen=1000,
        approximate=True,
    )
    await pipe.execute()
    return {
        "ok": True,
        "exiting_positions": len(open_positions),
        "halted_at": now_iso(),
    }


@router.post("/commands/global_resume")
async def global_resume(
    payload: ResumeRequest,
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true")
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "none")
    if payload.reset_daily_loss_circuit:
        pipe.set(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED, "false")
    pipe.xadd(
        K.SYSTEM_STREAM_CONTROL,
        {"event": "global_resume", "reason": payload.reason},
        maxlen=1000,
        approximate=True,
    )
    await pipe.execute()
    return {"ok": True, "trading_active": True}

