"""Single source of Redis pools (async + sync) shared by every engine.

Per `docs/Modular_Design.md` §3 + `docs/HLD.md`, Redis is reached over a
Unix socket only (no TCP). Async clients are used by FastAPI / Background /
Init / Scheduler / Health / Data Pipeline; sync clients are used by the
hot-path Strategy and Order Exec threads where coroutine overhead is
unwanted.

Usage:

    from state.redis_client import init_pools, get_redis, get_redis_sync

    init_pools()                     # once at engine startup
    r = get_redis()                  # async
    rs = get_redis_sync()            # sync

    await r.set("foo", "bar")
    rs.set("baz", "qux")

Lua scripts under `state/lua/` are loaded on demand via `load_script`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

import redis as _redis_sync
import redis.asyncio as _redis_async

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
_async_pool: _redis_async.ConnectionPool | None = None
_sync_pool: _redis_sync.ConnectionPool | None = None
_async_client: _redis_async.Redis | None = None
_sync_client: _redis_sync.Redis | None = None
_loaded_scripts: dict[str, Any] = {}

_DEFAULT_SOCKET: Final[str] = "/var/run/redis/redis-server.sock"
_LEGACY_SOCKET: Final[str] = "/var/run/redis/redis.sock"
_DEFAULT_MAX_CONNECTIONS: Final[int] = 32

_LUA_DIR: Final[Path] = Path(__file__).parent / "lua"


# ---------------------------------------------------------------------------
# Pool init / teardown
# ---------------------------------------------------------------------------
def init_pools(
    unix_socket_path: str | None = None,
    max_connections: int = _DEFAULT_MAX_CONNECTIONS,
    decode_responses: bool = True,
) -> None:
    """Initialise async + sync Redis pools.

    Connection target precedence:
        1. explicit `unix_socket_path` argument
        2. `REDIS_URL` env var (e.g. `unix:///var/run/redis/redis-server.sock`)
        3. default `/var/run/redis/redis-server.sock`

    Idempotent: subsequent calls are no-ops.
    """
    global _async_pool, _sync_pool, _async_client, _sync_client

    if _async_pool is not None and _sync_pool is not None:
        return

    socket_path = unix_socket_path or _resolve_socket_path()
    if not Path(socket_path).exists():
        if Path(_DEFAULT_SOCKET).exists():
            socket_path = _DEFAULT_SOCKET
        elif Path(_LEGACY_SOCKET).exists():
            socket_path = _LEGACY_SOCKET

    _async_pool = _redis_async.ConnectionPool(
        connection_class=_redis_async.UnixDomainSocketConnection,
        path=socket_path,
        max_connections=max_connections,
        decode_responses=decode_responses,
    )
    _sync_pool = _redis_sync.ConnectionPool(
        connection_class=_redis_sync.connection.UnixDomainSocketConnection,
        path=socket_path,
        max_connections=max_connections,
        decode_responses=decode_responses,
    )
    _async_client = _redis_async.Redis(connection_pool=_async_pool)
    _sync_client = _redis_sync.Redis(connection_pool=_sync_pool)


def _resolve_socket_path() -> str:
    raw = os.getenv("REDIS_URL", "").strip()
    if raw.startswith("redis+unix://"):
        raw = raw[len("redis+unix://") :]
    if raw.startswith("unix://"):
        raw = raw[len("unix://") :]
    if raw.startswith("/"):
        return raw.split("?", 1)[0]
    return _DEFAULT_SOCKET


def get_redis() -> _redis_async.Redis:
    """Return the async client. Call `init_pools()` first."""
    if _async_client is None:
        raise RuntimeError("Redis pools not initialised; call init_pools() first")
    return _async_client


def get_redis_sync() -> _redis_sync.Redis:
    """Return the sync client. Call `init_pools()` first."""
    if _sync_client is None:
        raise RuntimeError("Redis pools not initialised; call init_pools() first")
    return _sync_client


async def close_pools() -> None:
    """Close both pools. Idempotent; safe to call from a SIGTERM handler."""
    global _async_pool, _sync_pool, _async_client, _sync_client
    if _async_client is not None:
        await _async_client.aclose()
    if _async_pool is not None:
        await _async_pool.disconnect()
    if _sync_client is not None:
        _sync_client.close()
    if _sync_pool is not None:
        _sync_pool.disconnect()
    _async_pool = _sync_pool = None
    _async_client = _sync_client = None
    _loaded_scripts.clear()


# ---------------------------------------------------------------------------
# Test-injection helpers (used by tests/conftest.py to swap in fakeredis)
# ---------------------------------------------------------------------------
def set_clients_for_testing(async_client: Any, sync_client: Any) -> None:
    """Swap in test doubles. Internal — only for `tests/conftest.py`."""
    global _async_client, _sync_client
    _async_client = async_client
    _sync_client = sync_client


def reset_for_testing() -> None:
    """Forget pools and clients without closing them (tests do that)."""
    global _async_pool, _sync_pool, _async_client, _sync_client
    _async_pool = _sync_pool = None
    _async_client = _sync_client = None
    _loaded_scripts.clear()


# ---------------------------------------------------------------------------
# Lua loader
# ---------------------------------------------------------------------------
def load_script(name: str) -> Any:
    """Load a Lua script from `state/lua/{name}.lua`.

    Returns a `Script` object that can be invoked with `.execute(keys, args)`.
    Cached after first load; the SHA is registered with Redis automatically
    by redis-py.
    """
    if name in _loaded_scripts:
        return _loaded_scripts[name]

    path = _LUA_DIR / f"{name}.lua"
    if not path.is_file():
        raise FileNotFoundError(f"Lua script not found: {path}")

    source = path.read_text(encoding="utf-8")
    client = get_redis_sync()
    script = client.register_script(source)
    _loaded_scripts[name] = script
    return script


def clear_script_cache() -> None:
    """Forget all loaded scripts. Used between tests."""
    _loaded_scripts.clear()
