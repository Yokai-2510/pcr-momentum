"""Runtime configuration endpoints."""

from __future__ import annotations

from typing import Any

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from engines.api_gateway.auth import UserContext
from engines.api_gateway.deps import get_postgres, get_redis, require_admin
from engines.api_gateway.errors import APIError
from engines.api_gateway.util import coerce_jsonb, json_loads_maybe, now_iso
from state import keys as K
from state.config_loader import RUNTIME_CONFIG_REDIS_MAP, redis_key_for_config
from state.schemas.config import ExecutionConfig, IndexConfig, RiskConfig, SessionConfig

router = APIRouter(tags=["configs"], dependencies=[Depends(require_admin)])

_MODEL_BY_SECTION: dict[str, type[BaseModel]] = {
    "execution": ExecutionConfig,
    "session": SessionConfig,
    "risk": RiskConfig,
    "index:nifty50": IndexConfig,
    "index:banknifty": IndexConfig,
}


def _validate_section(section: str) -> str:
    if section not in _MODEL_BY_SECTION:
        raise APIError(404, "CONFIG_SECTION_NOT_FOUND", f"Unknown config section {section!r}")
    return section


async def _load_from_pg(pool: Any, section: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config_settings WHERE key = $1", section)
    if row is None:
        return None
    value = coerce_jsonb(row["value"])
    return value if isinstance(value, dict) else None


async def _load_section(redis: Any, pool: Any, section: str) -> dict[str, Any] | None:
    redis_key = redis_key_for_config(section)
    cached = json_loads_maybe(await redis.get(redis_key), None)
    if isinstance(cached, dict):
        return cached
    value = await _load_from_pg(pool, section)
    if value is not None:
        await redis.set(redis_key, orjson.dumps(value))
    return value


async def _publish_configs_view(redis: Any, pool: Any) -> None:
    payload = await get_configs(redis=redis, pool=pool)
    payload["ts"] = now_iso()
    pipe = redis.pipeline(transaction=False)
    pipe.set(K.UI_VIEW_CONFIGS, orjson.dumps(payload))
    pipe.publish(K.UI_PUB_VIEW, K.UI_VIEW_CONFIGS)
    await pipe.execute()


@router.get("/configs")
async def get_configs(
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    sections = {
        section: await _load_section(redis, pool, section)
        for section in RUNTIME_CONFIG_REDIS_MAP
    }
    return {
        "execution": sections.get("execution") or {},
        "session": sections.get("session") or {},
        "risk": sections.get("risk") or {},
        "indexes": {
            "nifty50": sections.get("index:nifty50") or {},
            "banknifty": sections.get("index:banknifty") or {},
        },
    }


@router.get("/configs/{section}")
async def get_config_section(
    section: str,
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    section = _validate_section(section)
    value = await _load_section(redis, pool, section)
    if value is None:
        raise APIError(404, "CONFIG_SECTION_NOT_FOUND", f"Config section {section!r} not found")
    return value


@router.put("/configs/{section}")
async def put_config_section(
    section: str,
    payload: dict[str, Any],
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
    user: UserContext = Depends(require_admin),
) -> dict[str, Any]:
    section = _validate_section(section)
    model = _MODEL_BY_SECTION[section]
    validated = model.model_validate(payload)
    value = validated.model_dump(mode="json")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO config_settings (key, value, updated_at, updated_by)
            VALUES ($1, $2::jsonb, now(), $3::uuid)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value,
                          updated_at = now(),
                          updated_by = EXCLUDED.updated_by
            """,
            section,
            orjson.dumps(value).decode(),
            user.id,
        )
    await redis.set(redis_key_for_config(section), orjson.dumps(value))
    await _publish_configs_view(redis, pool)
    return {"ok": True, "section": section, "updated_at": now_iso(), "value": value}
