"""FastAPI dependencies."""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from engines.api_gateway.auth import UserContext, decode_token
from engines.api_gateway.errors import APIError
from state.config_loader import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep() -> Settings:
    return get_settings()


def get_redis(request: Request) -> Any:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise APIError(503, "REDIS_UNAVAILABLE", "Redis client is not initialised")
    return redis


def get_postgres(request: Request) -> Any:
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise APIError(503, "POSTGRES_UNAVAILABLE", "Postgres pool is not initialised")
    return pool


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
) -> UserContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise APIError(401, "AUTH_REQUIRED", "Missing bearer token")
    try:
        return decode_token(creds.credentials, settings)
    except jwt.ExpiredSignatureError as exc:
        raise APIError(401, "TOKEN_EXPIRED", "JWT has expired") from exc
    except jwt.PyJWTError as exc:
        raise APIError(401, "TOKEN_INVALID", "JWT is invalid") from exc


def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if user.role != "admin":
        raise APIError(403, "FORBIDDEN", "Admin role required")
    return user

