"""Capital and broker kill-switch endpoints."""

from __future__ import annotations

from typing import Any

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from brokers.upstox import UpstoxAPI
from engines.api_gateway.deps import get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import json_loads_maybe, redis_get_json
from state import keys as K

router = APIRouter(tags=["capital"], dependencies=[Depends(require_admin)])


class KillSwitchToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment: str
    action: str


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    toggles: list[KillSwitchToggle] = Field(..., min_length=1)


async def _access_token(redis: Any) -> str:
    raw = await redis.get(K.USER_AUTH_ACCESS_TOKEN)
    parsed = json_loads_maybe(raw, None)
    if isinstance(parsed, dict) and parsed.get("token"):
        return str(parsed["token"])
    if isinstance(raw, bytes):
        return raw.decode()
    if isinstance(raw, str) and raw:
        return raw
    raise APIError(503, "AUTH_TOKEN_MISSING", "Upstox access token is not available")


@router.get("/capital/funds")
async def capital_funds(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    cached = await redis_get_json(redis, K.USER_CAPITAL_FUNDS, None)
    if isinstance(cached, dict):
        return cached
    token = await _access_token(redis)
    res = UpstoxAPI.get_capital({"access_token": token})
    if not res.get("success"):
        raise APIError(503, "BROKER_ERROR", "Broker capital request failed", {"broker": res})
    await redis.set(K.USER_CAPITAL_FUNDS, orjson.dumps(res.get("data") or {}))
    return res.get("data") or {}


@router.get("/capital/kill_switch")
async def capital_kill_switch(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    cached = await redis_get_json(redis, K.USER_CAPITAL_KILL_SWITCH, None)
    if isinstance(cached, dict):
        return cached
    if isinstance(cached, list):
        return {"segments": cached}
    token = await _access_token(redis)
    res = UpstoxAPI.get_kill_switch_status({"access_token": token})
    if not res.get("success"):
        raise APIError(503, "BROKER_ERROR", "Broker kill-switch request failed", {"broker": res})
    data = {"segments": res.get("data") or []}
    await redis.set(K.USER_CAPITAL_KILL_SWITCH, orjson.dumps(data))
    return data


@router.post("/capital/kill_switch")
async def set_capital_kill_switch(
    payload: KillSwitchRequest,
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    token = await _access_token(redis)
    toggles = [t.model_dump() for t in payload.toggles]
    res = UpstoxAPI.set_kill_switch({"access_token": token, "toggles": toggles})
    if not res.get("success"):
        raise APIError(503, "BROKER_ERROR", "Broker kill-switch update failed", {"broker": res})
    data = {"segments": res.get("data") or []}
    await redis.set(K.USER_CAPITAL_KILL_SWITCH, orjson.dumps(data))
    return data

