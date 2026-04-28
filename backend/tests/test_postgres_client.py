"""Tests for `state/postgres_client.py`.

Most cases are unit-level (no live Postgres). The `live_*` tests require
a `DATABASE_URL` and are skipped automatically otherwise.
"""

from __future__ import annotations

import os

import pytest

from state import postgres_client


class TestPoolLifecycleNoDb:
    def test_get_pool_before_init_raises(self) -> None:
        postgres_client.set_pool_for_testing(None)
        with pytest.raises(RuntimeError, match="not initialised"):
            postgres_client.get_pool()

    async def test_init_pool_requires_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        postgres_client.set_pool_for_testing(None)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DSN not provided"):
            await postgres_client.init_pool()

    async def test_close_pool_when_uninit_is_noop(self) -> None:
        postgres_client.set_pool_for_testing(None)
        await postgres_client.close_pool()  # must not raise


@pytest.mark.integration
class TestLivePostgres:
    @pytest.fixture(autouse=True)
    def _gate(self, integration_db_url: str | None) -> None:
        if not integration_db_url:
            pytest.skip("DATABASE_URL not set; skipping live Postgres test")

    async def test_init_ping_close(self) -> None:
        postgres_client.set_pool_for_testing(None)
        pool = await postgres_client.init_pool(os.environ["DATABASE_URL"])
        try:
            assert pool is postgres_client.get_pool()
            assert await postgres_client.ping() is True
        finally:
            await postgres_client.close_pool()

    async def test_transaction_helper(self) -> None:
        postgres_client.set_pool_for_testing(None)
        await postgres_client.init_pool(os.environ["DATABASE_URL"])
        try:
            async with postgres_client.transaction() as conn:
                value = await conn.fetchval("SELECT 7 + 11")
            assert value == 18
        finally:
            await postgres_client.close_pool()
