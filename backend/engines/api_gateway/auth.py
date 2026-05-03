"""JWT and password helpers for the single-operator gateway."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from state.config_loader import Settings

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_TTL_HOURS = 24


@dataclass(frozen=True)
class UserContext:
    id: str
    username: str
    role: str


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_password_for_testing(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def issue_token(
    user: UserContext,
    settings: Settings,
    *,
    ttl_hours: int = DEFAULT_TOKEN_TTL_HOURS,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    exp = now + timedelta(hours=ttl_hours)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)
    return {
        "token": token,
        "expires_at": exp.isoformat(),
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


def decode_token(token: str, settings: Settings) -> UserContext:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        raise jwt.ExpiredSignatureError("token expired")
    return UserContext(
        id=str(payload["sub"]),
        username=str(payload.get("username") or ""),
        role=str(payload.get("role") or "admin"),
    )

