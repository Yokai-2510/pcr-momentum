"""
engines.health.probes — pure probe coroutines + classifier.

Each probe returns a tuple `(status, detail)` where status is one of
`"green" | "yellow" | "red"` and detail is a short string. Probes never
raise — failures are caught and reported as red so a single failure
cannot wedge the loop.
"""

from __future__ import annotations

import time
from typing import Any

import asyncpg
import psutil
import redis.asyncio as _redis_async

from brokers.upstox import UpstoxAPI
from state import keys as K

ProbeResult = tuple[str, str]

# How stale a heartbeat / ws_status payload may be before we paint it red.
ENGINE_HEARTBEAT_RED_AFTER_SEC = 30
ENGINE_HEARTBEAT_YELLOW_AFTER_SEC = 15
WS_RED_AFTER_SEC = 30


def _now_ms() -> int:
    return int(time.time() * 1000)


async def probe_redis(redis_async: _redis_async.Redis) -> ProbeResult:
    try:
        pong = await redis_async.ping()  # type: ignore[misc]
        return ("green", "ok") if pong else ("red", "no_pong")
    except Exception as e:
        return "red", f"ping_failed:{e!r}"


async def probe_postgres(pool: asyncpg.Pool | None) -> ProbeResult:
    if pool is None:
        return "red", "no_pool"
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1")
            return ("green", "ok") if row == 1 else ("red", "unexpected_row")
    except Exception as e:
        return "red", f"select_failed:{e!r}"


def probe_broker_rest() -> ProbeResult:
    """Synchronous because UpstoxAPI is sync-only. Caller wraps in to_thread."""
    try:
        res = UpstoxAPI.get_market_status({"exchange": "NSE"})
    except Exception as e:
        return "red", f"raised:{e!r}"
    if res.get("success"):
        return "green", "ok"
    return "yellow", str(res.get("error") or "rest_error")


async def probe_broker_ws(redis_async: _redis_async.Redis) -> ProbeResult:
    raw = await redis_async.hgetall(K.MARKET_DATA_WS_STATUS_MARKET)  # type: ignore[misc]
    if not raw:
        return "red", "no_ws_status"
    decoded: dict[str, str] = {}
    for k, v in raw.items():
        ks = k.decode() if isinstance(k, bytes) else str(k)
        vs = v.decode() if isinstance(v, bytes) else str(v)
        decoded[ks] = vs
    last_ts_ms = int(decoded.get("ts_ms") or 0)
    if last_ts_ms == 0:
        return "red", "no_ts"
    age_sec = (_now_ms() - last_ts_ms) / 1000.0
    state = decoded.get("state") or ""
    if state.lower() in ("disconnected", "down", "error"):
        return "red", f"state={state}"
    if age_sec > WS_RED_AFTER_SEC:
        return "red", f"stale:{age_sec:.1f}s"
    if age_sec > WS_RED_AFTER_SEC / 2:
        return "yellow", f"stale:{age_sec:.1f}s"
    return "green", f"state={state};age={age_sec:.1f}s"


def probe_system_load() -> ProbeResult:
    try:
        load1, _load5, _load15 = psutil.getloadavg()
    except Exception as e:
        return "red", f"loadavg_failed:{e!r}"
    cpus = psutil.cpu_count(logical=True) or 1
    norm = load1 / cpus
    if norm >= 0.95:
        return "red", f"load1={load1:.2f}/{cpus}"
    if norm >= 0.75:
        return "yellow", f"load1={load1:.2f}/{cpus}"
    return "green", f"load1={load1:.2f}/{cpus}"


def probe_swap() -> ProbeResult:
    try:
        sw = psutil.swap_memory()
    except Exception as e:
        return "red", f"swap_read_failed:{e!r}"
    pct = float(sw.percent or 0)
    if pct >= 80:
        return "red", f"swap={pct:.1f}%"
    if pct >= 50:
        return "yellow", f"swap={pct:.1f}%"
    return "green", f"swap={pct:.1f}%"


async def probe_engines(redis_async: _redis_async.Redis) -> dict[str, ProbeResult]:
    """Returns per-engine ProbeResult based on heartbeat freshness."""
    raw = await redis_async.hgetall(K.SYSTEM_HEALTH_HEARTBEATS)  # type: ignore[misc]
    if not raw:
        return {}
    out: dict[str, ProbeResult] = {}
    now_ms = _now_ms()
    for k, v in raw.items():
        engine = k.decode() if isinstance(k, bytes) else str(k)
        ts_str = v.decode() if isinstance(v, bytes) else str(v)
        try:
            ts_ms = int(ts_str)
        except ValueError:
            out[engine] = ("red", "bad_ts")
            continue
        age = (now_ms - ts_ms) / 1000.0
        if age > ENGINE_HEARTBEAT_RED_AFTER_SEC:
            out[engine] = ("red", f"stale:{age:.1f}s")
        elif age > ENGINE_HEARTBEAT_YELLOW_AFTER_SEC:
            out[engine] = ("yellow", f"stale:{age:.1f}s")
        else:
            out[engine] = ("green", f"age:{age:.1f}s")
    return out


def aggregate_status(parts: list[ProbeResult]) -> str:
    """Worst-of: red > yellow > green."""
    levels = {p[0] for p in parts}
    if "red" in levels:
        return "red"
    if "yellow" in levels:
        return "yellow"
    if "green" in levels:
        return "green"
    return "red"


def to_dict(result: ProbeResult) -> dict[str, Any]:
    return {"status": result[0], "detail": result[1], "ts_ms": _now_ms()}
