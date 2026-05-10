"""Tests for `state/redis_client.py` — pool management + socket resolution."""

from __future__ import annotations

import pytest

from state import redis_client


class TestPoolLifecycle:
    def test_get_redis_before_init_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not initialised"):
            redis_client.get_redis()

    def test_get_redis_sync_before_init_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not initialised"):
            redis_client.get_redis_sync()

    def test_set_and_get_after_test_injection(self, fake_redis_pair: tuple[object, object]) -> None:
        async_client, sync_client = fake_redis_pair
        # The fixture installs both into the module
        assert redis_client.get_redis() is async_client
        assert redis_client.get_redis_sync() is sync_client


class TestSocketResolution:
    def test_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert redis_client._resolve_socket_path().endswith("redis-server.sock")

    def test_unix_url_strips_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL", "unix:///var/run/redis/redis-server.sock")
        assert redis_client._resolve_socket_path() == "/var/run/redis/redis-server.sock"

    def test_plain_path_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL", "/run/redis/test.sock")
        assert redis_client._resolve_socket_path() == "/run/redis/test.sock"
