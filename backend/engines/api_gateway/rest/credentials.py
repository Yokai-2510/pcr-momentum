"""Upstox credential management endpoints."""

from __future__ import annotations

from typing import Any

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from brokers.upstox import UpstoxAPI
from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.util import json_loads_maybe, mask_secret
from state import keys as K
from state.crypto import encrypt_json

router = APIRouter(tags=["credentials"], dependencies=[Depends(require_admin)])


class UpstoxCredentialsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1)
    api_secret: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)
    totp_secret: str = Field(..., min_length=1)
    mobile_no: str = Field(..., min_length=6)
    pin: str = Field(..., min_length=4)
    analytics_token: str | None = None
    sandbox_token: str | None = None


async def _read_creds(redis: Any) -> dict[str, Any]:
    raw = await redis.get(K.USER_CREDENTIALS_UPSTOX)
    parsed = json_loads_maybe(raw, {})
    return parsed if isinstance(parsed, dict) else {}


def _masked(creds: dict[str, Any], auth_status: str) -> dict[str, Any]:
    token = creds.get("access_token")
    return {
        "configured": bool(creds),
        "auth_status": auth_status or "unknown",
        "api_key": mask_secret(creds.get("api_key")),
        "api_secret": mask_secret(creds.get("api_secret"), fixed="****"),
        "redirect_uri": creds.get("redirect_uri"),
        "totp_secret": mask_secret(creds.get("totp_secret"), fixed="****"),
        "mobile_no": mask_secret(creds.get("mobile_no"), keep=4),
        "pin": mask_secret(creds.get("pin"), fixed="****"),
        "analytics_token": mask_secret(creds.get("analytics_token")),
        "sandbox_token": mask_secret(creds.get("sandbox_token")),
        "access_token": {
            "present": bool(token),
            "expires_at": creds.get("access_token_expires_at"),
            "source": "webhook_v3" if token else None,
        },
    }


@router.get("/credentials/upstox")
async def get_upstox_credentials(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    creds = await _read_creds(redis)
    auth_status_raw = await redis.get(K.SYSTEM_HEALTH_AUTH)
    auth_status = auth_status_raw.decode() if isinstance(auth_status_raw, bytes) else str(auth_status_raw or "unknown")
    return _masked(creds, auth_status)


@router.post("/credentials/upstox")
async def set_upstox_credentials(
    payload: UpstoxCredentialsIn,
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    creds = payload.model_dump()
    existing = await _read_creds(redis)
    if existing.get("access_token"):
        creds["access_token"] = existing["access_token"]
        creds["access_token_expires_at"] = existing.get("access_token_expires_at")

    encrypted = encrypt_json(creds)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_credentials (broker, encrypted_value, updated_at)
            VALUES ('upstox', $1, now())
            ON CONFLICT (broker)
            DO UPDATE SET encrypted_value = EXCLUDED.encrypted_value,
                          updated_at = now()
            """,
            encrypted,
        )

    probe_token = creds.get("analytics_token") or creds.get("access_token")
    profile: dict[str, Any] | None = None
    broker_error: Any = None
    auth_status = "missing"
    if probe_token:
        res = UpstoxAPI.get_profile({"access_token": probe_token, "timeout": 5})
        if res.get("success"):
            auth_status = "valid"
            profile = res.get("data")
        else:
            auth_status = "invalid"
            broker_error = res.get("error") or res
    else:
        auth_status = "invalid"
        broker_error = {"code": "TOKEN_MISSING", "message": "No analytics/access token provided"}

    reason = "none" if auth_status == "valid" else "auth_invalid"
    trading_active = auth_status == "valid"
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.USER_CREDENTIALS_UPSTOX, orjson.dumps(creds))
    pipe.set(K.SYSTEM_HEALTH_AUTH, auth_status)
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, reason)
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "true" if trading_active else "false")
    pipe.publish(
        K.SYSTEM_PUB_SYSTEM_EVENT,
        '{"event":"auth_recovered"}' if trading_active else '{"event":"auth_invalid"}',
    )
    await pipe.execute()

    out: dict[str, Any] = {
        "ok": True,
        "auth_status": auth_status,
        "trading_active": trading_active,
        "trading_disabled_reason": reason,
    }
    if profile is not None:
        out["profile"] = profile
    if broker_error is not None:
        out["broker_error"] = broker_error
    return out


@router.delete("/credentials/upstox")
async def delete_upstox_credentials(
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, str | bool]:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_credentials WHERE broker = 'upstox'")
    pipe = redis.pipeline(transaction=False)
    pipe.delete(K.USER_CREDENTIALS_UPSTOX)
    pipe.delete(K.USER_AUTH_ACCESS_TOKEN)
    pipe.set(K.SYSTEM_HEALTH_AUTH, "missing")
    pipe.set(K.SYSTEM_FLAGS_TRADING_DISABLED_REASON, "awaiting_credentials")
    pipe.set(K.SYSTEM_FLAGS_TRADING_ACTIVE, "false")
    await pipe.execute()
    return {"ok": True, "auth_status": "missing"}
