"""Small Redis/Postgres helpers for API routers."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import orjson


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def json_loads_maybe(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, bytes | bytearray | str):
        try:
            return orjson.loads(raw)
        except Exception:
            return default
    return raw


async def redis_get_json(redis: Any, key: str, default: Any = None) -> Any:
    return json_loads_maybe(await redis.get(key), default)


async def redis_set_json(redis: Any, key: str, payload: Any) -> None:
    await redis.set(key, orjson.dumps(payload))


async def redis_hgetall_decoded(redis: Any, key: str) -> dict[str, str]:
    raw = await redis.hgetall(key)
    return {decode(k): decode(v) for k, v in raw.items()}


async def redis_smembers_decoded(redis: Any, key: str) -> set[str]:
    raw = await redis.smembers(key)
    return {decode(v) for v in raw}


def mask_secret(value: Any, *, keep: int = 4, fixed: str | None = None) -> str | None:
    if value is None or value == "":
        return None
    if fixed is not None:
        return fixed
    text = str(value)
    suffix = text[-keep:] if len(text) > keep else text
    return "****" + suffix


def row_to_dict(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row:
        value = row[key]
        if isinstance(value, datetime | date):
            out[key] = value.isoformat()
        elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            out[key] = None
        else:
            out[key] = value
    return out


def coerce_jsonb(value: Any) -> Any:
    return json_loads_maybe(value, value)
