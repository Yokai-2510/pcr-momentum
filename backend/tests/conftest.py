"""Pytest fixtures.

`fakeredis` provides Lua-capable in-memory Redis for unit tests; tests
that need a real Postgres are marked `@pytest.mark.integration` and
skipped automatically when `DATABASE_URL` is not set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio

from state import redis_client


@pytest.fixture(autouse=True)
def _reset_redis_module() -> Iterator[None]:
    """Forget any cached pools/scripts between tests."""
    redis_client.reset_for_testing()
    redis_client.clear_script_cache()
    yield
    redis_client.reset_for_testing()
    redis_client.clear_script_cache()


@pytest_asyncio.fixture
async def fake_redis_async() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Async fakeredis client with Lua + decoded responses."""
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def fake_redis_sync() -> Iterator[fakeredis.FakeRedis]:
    """Sync fakeredis client sharing one server with the async fixture."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def fake_redis_pair() -> Iterator[tuple[fakeredis.aioredis.FakeRedis, fakeredis.FakeRedis]]:
    """Async + sync clients sharing the same fakeredis server."""
    server = fakeredis.FakeServer()
    async_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync_client = fakeredis.FakeRedis(server=server, decode_responses=True)
    redis_client.set_clients_for_testing(async_client, sync_client)
    try:
        yield (async_client, sync_client)
    finally:
        sync_client.close()


@pytest.fixture(scope="session")
def integration_db_url() -> str | None:
    """Return DATABASE_URL if set; otherwise tests dependent on it skip."""
    return os.getenv("DATABASE_URL")


@pytest.fixture(scope="session")
def migration_db_url() -> str | None:
    """Return MIGRATION_TEST_DATABASE_URL when migration integration tests are enabled."""
    return os.getenv("MIGRATION_TEST_DATABASE_URL")


@pytest.fixture
def env_required(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the minimal env vars Settings requires for unit tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/z")
    monkeypatch.setenv("REDIS_URL", "unix:///tmp/test.sock")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CREDS_ENCRYPTION_KEY", "y" * 44)
    monkeypatch.setenv("APP_ENV", "dev")
    yield
