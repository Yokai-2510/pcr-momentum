"""Authentication and Upstox token webhook endpoints."""

from __future__ import annotations

from typing import Any

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from engines.api_gateway.auth import UserContext, issue_token, verify_password
from engines.api_gateway.deps import get_current_user, get_postgres, get_redis, get_settings_dep
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import json_loads_maybe
from state import keys as K
from state.config_loader import Settings
from state.crypto import encrypt_json

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UpstoxWebhookRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_id: str
    user_id: str | None = None
    access_token: str
    token_type: str | None = None
    expires_at: str | None = None
    issued_at: str | None = None
    message_type: str | None = None


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    pool: Any = Depends(get_postgres),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, password_hash, role
            FROM user_accounts
            WHERE username = $1
            """,
            payload.username,
        )
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid username or password")
    user = UserContext(id=str(row["id"]), username=row["username"], role=row["role"] or "admin")
    return issue_token(user, settings)


@router.post("/auth/refresh")
async def refresh(
    user: UserContext = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    return issue_token(user, settings)


@router.post("/auth/upstox-webhook")
async def upstox_webhook(
    payload: UpstoxWebhookRequest,
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, bool]:
    raw_creds = await redis.get(K.USER_CREDENTIALS_UPSTOX)
    creds = json_loads_maybe(raw_creds, {})
    if not isinstance(creds, dict) or not creds:
        raise APIError(400, "INVALID_WEBHOOK_PAYLOAD", "No stored Upstox credentials")
    if str(creds.get("api_key") or "") != payload.client_id:
        raise APIError(400, "INVALID_WEBHOOK_PAYLOAD", "Webhook client_id does not match")

    creds["access_token"] = payload.access_token
    creds["access_token_expires_at"] = payload.expires_at
    token_payload = {
        "token": payload.access_token,
        "issued_at": payload.issued_at,
        "expires_at": payload.expires_at,
        "source": "webhook_v3",
    }
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

    pipe = redis.pipeline(transaction=False)
    pipe.set(K.USER_CREDENTIALS_UPSTOX, orjson.dumps(creds))
    pipe.set(K.USER_AUTH_ACCESS_TOKEN, orjson.dumps(token_payload))
    pipe.set(K.SYSTEM_HEALTH_AUTH, "valid")
    pipe.publish(K.SYSTEM_PUB_SYSTEM_EVENT, '{"event":"auth_refreshed"}')
    await pipe.execute()
    return {"ok": True}

