"""Operational command endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from brokers.upstox import UpstoxAPI
from engines.api_gateway.deps import get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import json_loads_maybe
from engines.background import instrument_refresh
from state import keys as K

router = APIRouter(tags=["commands"], dependencies=[Depends(require_admin)])


@router.post("/commands/instrument_refresh")
async def command_instrument_refresh(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    result = await instrument_refresh.refresh(redis)
    return {"ok": True, "indexes": result}


@router.post("/commands/upstox_token_request")
async def command_upstox_token_request(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    raw = await redis.get(K.USER_CREDENTIALS_UPSTOX)
    creds = json_loads_maybe(raw, {})
    if not isinstance(creds, dict) or not creds:
        raise APIError(400, "CREDENTIALS_MISSING", "Upstox credentials are not configured")
    res = UpstoxAPI.request_access_token({"creds": creds})
    if not res.get("success"):
        raise APIError(503, "BROKER_ERROR", "Access-token request failed", {"broker": res})
    data = res.get("data") or {}
    return {
        "ok": True,
        "authorization_expiry": data.get("authorization_expiry"),
        "notifier_url": data.get("notifier_url"),
        "message": "Approve the request in your Upstox app or WhatsApp; the token will arrive at the notifier webhook.",
    }

