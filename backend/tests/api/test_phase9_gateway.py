"""Phase 9 API Gateway integration tests against in-memory fakes."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
import pytest

from engines.api_gateway.auth import hash_password_for_testing
from engines.api_gateway.main import create_app
from engines.api_gateway.view_router import snapshot
from state import keys as K
from state.config_loader import reset_settings_cache
from state.crypto import reset_cache_for_testing


class FakeRow(dict[str, Any]):
    pass


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


class FakeConn:
    def __init__(self) -> None:
        self.user = FakeRow(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "username": "admin",
                "password_hash": hash_password_for_testing("secret"),
                "role": "admin",
            }
        )
        self.configs: dict[str, dict[str, Any]] = {
            "execution": {
                "buffer_inr": 2,
                "eod_buffer_inr": 5,
                "spread_skip_pct": 0.05,
                "drift_threshold_inr": 3,
                "chase_ceiling_inr": 15,
                "open_timeout_sec": 8,
                "partial_grace_sec": 3,
                "max_retries": 2,
                "worker_pool_size": 8,
                "liquidity_exit_suppress_after": "15:00",
            },
            "session": {"market_open": "09:15"},
            "risk": {
                "daily_loss_circuit_pct": 0.08,
                "max_concurrent_positions": 2,
                "trading_capital_inr": 200000,
            },
            "index:nifty50": {
                "index": "nifty50",
                "strike_step": 50,
                "lot_size": 75,
                "exchange": "NFO",
                "pre_open_subscribe_window": 6,
                "trading_basket_range": 2,
                "reversal_threshold_inr": 20,
                "entry_dominance_threshold_inr": 20,
                "post_sl_cooldown_sec": 60,
                "post_reversal_cooldown_sec": 90,
                "max_entries_per_day": 8,
                "max_reversals_per_day": 4,
                "qty_lots": 1,
                "sl_pct": 0.2,
                "target_pct": 0.5,
                "tsl_arm_pct": 0.15,
                "tsl_trail_pct": 0.05,
                "max_hold_sec": 1500,
                "delta_pcr_required_for_entry": False,
            },
            "index:banknifty": {
                "index": "banknifty",
                "strike_step": 100,
                "lot_size": 35,
                "exchange": "NFO",
                "pre_open_subscribe_window": 6,
                "trading_basket_range": 2,
                "reversal_threshold_inr": 40,
                "entry_dominance_threshold_inr": 40,
                "post_sl_cooldown_sec": 60,
                "post_reversal_cooldown_sec": 90,
                "max_entries_per_day": 8,
                "max_reversals_per_day": 4,
                "qty_lots": 1,
                "sl_pct": 0.2,
                "target_pct": 0.5,
                "tsl_arm_pct": 0.15,
                "tsl_trail_pct": 0.05,
                "max_hold_sec": 1500,
                "delta_pcr_required_for_entry": False,
            },
        }
        self.credentials: bytes | None = None

    async def fetchrow(self, sql: str, *args: Any) -> FakeRow | None:
        if "FROM user_accounts" in sql:
            return self.user if args and args[0] == "admin" else None
        if "FROM config_settings" in sql:
            value = self.configs.get(args[0])
            return FakeRow({"value": value}) if value is not None else None
        if "FROM trades_closed_positions" in sql:
            return None
        return None

    async def fetch(self, _sql: str, *_args: Any) -> list[FakeRow]:
        return []

    async def fetchval(self, sql: str, *_args: Any) -> int:
        if "count(*) FROM trades_closed_positions" in sql:
            return 0
        return 1

    async def execute(self, sql: str, *args: Any) -> str:
        if "config_settings" in sql:
            self.configs[args[0]] = orjson.loads(args[1])
        if "user_credentials" in sql and "DELETE" not in sql:
            self.credentials = args[0]
        if "DELETE FROM user_credentials" in sql:
            self.credentials = None
        return "OK"


class FakePubSub:
    async def subscribe(self, *_channels: str) -> None:
        return None

    async def unsubscribe(self, *_channels: str) -> None:
        return None

    async def get_message(self, **_kwargs: Any) -> None:
        return None

    async def close(self) -> None:
        return None


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> FakePipeline:
            self.ops.append((name, args, kwargs))
            return self

        return _record

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for name, args, kwargs in self.ops:
            result = getattr(self.redis, name)(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            out.append(result)
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, Any] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[str]] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}

    def pipeline(self, *_args: Any, **_kwargs: Any) -> FakePipeline:
        return FakePipeline(self)

    def pubsub(self) -> FakePubSub:
        return FakePubSub()

    async def get(self, key: str) -> Any:
        return self.strings.get(key)

    async def set(self, key: str, value: Any, **_kwargs: Any) -> bool:
        self.strings[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            count += int(self.strings.pop(key, None) is not None)
            count += int(self.hashes.pop(key, None) is not None)
            count += int(self.sets.pop(key, None) is not None)
        return count

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict[str, Any] | None = None) -> int:
        target = self.hashes.setdefault(key, {})
        if mapping:
            target.update(mapping)
        elif field is not None:
            target[field] = value
        return 1

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def sadd(self, key: str, *values: Any) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(str(v) for v in values)
        return len(target) - before

    async def xadd(self, key: str, fields: dict[str, Any], **_kwargs: Any) -> str:
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"

    async def publish(self, *_args: Any) -> int:
        return 1

    async def lrange(self, *_args: Any) -> list[Any]:
        return []


@pytest.fixture
def api_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, FakeRedis, FakePool]]:
    key = base64.b64encode(b"1" * 32).decode()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("REDIS_URL", "unix:///tmp/redis.sock")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("CREDS_ENCRYPTION_KEY", key)
    monkeypatch.setenv("APP_ENV", "dev")
    reset_settings_cache()
    reset_cache_for_testing()

    app = create_app(init_resources=False)
    redis = FakeRedis()
    pool = FakePool()
    app.state.redis = redis
    app.state.pg_pool = pool
    yield app, redis, pool


def _client(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _token(client: httpx.AsyncClient) -> str:
    res = await client.post("/auth/login", json={"username": "admin", "password": "secret"})
    assert res.status_code == 200
    return str(res.json()["token"])


def test_auth_login_refresh_and_protected_route(api_app: tuple[Any, FakeRedis, FakePool]) -> None:
    app, _redis, _pool = api_app

    async def _run() -> None:
        async with _client(app) as client:
            assert (await client.get("/configs")).status_code == 401
            token = await _token(client)
            res = await client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 200
            assert res.json()["user"]["username"] == "admin"

    asyncio.run(_run())


def test_configs_read_and_write_through(api_app: tuple[Any, FakeRedis, FakePool]) -> None:
    app, redis, pool = api_app

    async def _run() -> None:
        async with _client(app) as client:
            token = await _token(client)
            headers = {"Authorization": f"Bearer {token}"}
            res = await client.get("/configs", headers=headers)
            assert res.status_code == 200
            assert res.json()["risk"]["trading_capital_inr"] == 200000

            updated = dict(pool.conn.configs["risk"])
            updated["trading_capital_inr"] = 250000
            res = await client.put("/configs/risk", json=updated, headers=headers)
            assert res.status_code == 200
            assert pool.conn.configs["risk"]["trading_capital_inr"] == 250000
            assert K.STRATEGY_CONFIGS_RISK in redis.strings

    asyncio.run(_run())


def test_strategy_commands_and_manual_exit(api_app: tuple[Any, FakeRedis, FakePool]) -> None:
    app, redis, _pool = api_app

    async def _run() -> None:
        async with _client(app) as client:
            token = await _token(client)
            headers = {"Authorization": f"Bearer {token}"}

            halt = await client.post("/commands/halt_index/nifty50", headers=headers)
            assert halt.json()["enabled"] is False
            assert redis.strings[K.strategy_enabled("nifty50")] == "false"

            pos_id = "pos-1"
            redis.hashes[K.orders_position(pos_id)] = {"pos_id": pos_id, "index": "nifty50"}
            res = await client.post(
                f"/commands/manual_exit/{pos_id}",
                json={"reason": "operator test"},
                headers=headers,
            )
            assert res.status_code == 200
            assert redis.streams[K.ORDERS_STREAM_MANUAL_EXIT][0]["position_id"] == pos_id

    asyncio.run(_run())


def test_health_is_public(api_app: tuple[Any, FakeRedis, FakePool]) -> None:
    app, redis, _pool = api_app
    redis.hashes[K.SYSTEM_HEALTH_SUMMARY] = {"status": "green"}

    async def _run() -> None:
        async with _client(app) as client:
            res = await client.get("/health")
            assert res.status_code == 200
            assert res.json()["summary"] == "OK"

    asyncio.run(_run())


def test_credentials_mask_and_delete(
    api_app: tuple[Any, FakeRedis, FakePool], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, redis, pool = api_app
    monkeypatch.setattr(
        "engines.api_gateway.rest.credentials.UpstoxAPI.get_profile",
        lambda _params: {"success": True, "data": {"user_id": "u1"}},
    )
    payload = {
        "api_key": "abcdef1234",
        "api_secret": "secret",
        "redirect_uri": "https://example.test/webhook",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "mobile_no": "9876543210",
        "pin": "123456",
        "analytics_token": "token1234",
    }
    async def _run() -> None:
        async with _client(app) as client:
            token = await _token(client)
            headers = {"Authorization": f"Bearer {token}"}
            res = await client.post("/credentials/upstox", json=payload, headers=headers)
            assert res.status_code == 200
            assert pool.conn.credentials is not None
            masked = (await client.get("/credentials/upstox", headers=headers)).json()
            assert masked["api_key"] == "****1234"
            assert masked["mobile_no"] == "****3210"

            res = await client.delete("/credentials/upstox", headers=headers)
            assert res.status_code == 200
            assert K.USER_CREDENTIALS_UPSTOX not in redis.strings

    asyncio.run(_run())


def test_stream_route_and_snapshot_builder(api_app: tuple[Any, FakeRedis, FakePool]) -> None:
    app, redis, _pool = api_app
    redis.strings[K.UI_VIEW_DASHBOARD] = orjson.dumps(
        {"ts": datetime.now(UTC).isoformat(), "system_state": {"mode": "paper"}}
    )
    assert any(getattr(route, "path", "") == "/stream" for route in app.routes)

    async def _run() -> None:
        snap = await snapshot(redis, ["dashboard"])
        assert snap["dashboard"]["system_state"]["mode"] == "paper"

    asyncio.run(_run())
