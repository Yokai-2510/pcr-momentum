"""Health endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from engines.api_gateway.deps import get_postgres, get_redis
from engines.api_gateway.util import json_loads_maybe, redis_get_json, redis_hgetall_decoded
from engines.health import probes
from state import keys as K

router = APIRouter(tags=["health"])


def _probe_to_public(value: dict[str, Any]) -> str:
    status = str(value.get("status") or "red")
    if status == "green":
        return "OK"
    if status == "yellow":
        return "DEGRADED"
    return "DOWN"


@router.get("/health")
async def health(redis: Any = Depends(get_redis)) -> dict[str, Any]:
    view = await redis_get_json(redis, K.UI_VIEW_HEALTH, None)
    if isinstance(view, dict):
        return view

    summary_hash = await redis_hgetall_decoded(redis, K.SYSTEM_HEALTH_SUMMARY)
    deps_hash = await redis_hgetall_decoded(redis, K.SYSTEM_HEALTH_DEPENDENCIES)
    engines_hash = await redis_hgetall_decoded(redis, K.SYSTEM_HEALTH_ENGINES)
    hb_hash = await redis_hgetall_decoded(redis, K.SYSTEM_HEALTH_HEARTBEATS)

    engines: dict[str, Any] = {}
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    for name in K.HEARTBEAT_FIELDS:
        ts_raw = hb_hash.get(name)
        ts_ms = int(ts_raw) if ts_raw and ts_raw.isdigit() else 0
        engines[name] = {
            "alive": bool(ts_ms and now_ms - ts_ms < 30_000),
            "last_hb_ts": ts_ms or None,
        }
    for name, raw in engines_hash.items():
        parsed = json_loads_maybe(raw, {})
        if isinstance(parsed, dict):
            engines[name] = {
                "alive": parsed.get("status") == "green",
                "last_hb_ts": parsed.get("ts_ms"),
                "note": parsed.get("detail"),
            }

    dependencies: dict[str, Any] = {}
    for name, raw in deps_hash.items():
        parsed = json_loads_maybe(raw, {})
        dependencies[name] = _probe_to_public(parsed) if isinstance(parsed, dict) else "UNKNOWN"

    status = summary_hash.get("status", "UNKNOWN").lower()
    summary = {"green": "OK", "yellow": "DEGRADED", "red": "DOWN"}.get(
        status, status.upper()
    )
    return {
        "summary": summary,
        "engines": engines,
        "dependencies": dependencies,
        "alerts": [],
        "ts": datetime.now(UTC).isoformat(),
    }


@router.get("/health/dependencies/test")
async def health_dependencies_test(
    redis: Any = Depends(get_redis),
    pool: Any = Depends(get_postgres),
) -> dict[str, Any]:
    redis_res = await probes.probe_redis(redis)
    pg_res = await probes.probe_postgres(pool)
    broker_res = probes.probe_broker_rest()
    return {
        "redis": {"ok": redis_res[0] == "green", "detail": redis_res[1]},
        "postgres": {"ok": pg_res[0] == "green", "detail": pg_res[1]},
        "broker": {
            "ok": broker_res[0] == "green",
            "detail": broker_res[1],
            "auth_valid": broker_res[0] == "green",
            "kill_switch_clear": True,
        },
    }
