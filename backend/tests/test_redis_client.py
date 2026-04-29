"""Tests for `state/redis_client.py` — pool management + Lua loader."""

from __future__ import annotations

from pathlib import Path

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


class TestLuaLoader:
    def test_load_script_round_trip(self, fake_redis_pair: tuple[object, object]) -> None:
        # Use a real script from the package
        script = redis_client.load_script("config_write_through")
        assert script is not None
        # Loading again returns the cached instance (same object)
        assert redis_client.load_script("config_write_through") is script

    def test_load_script_missing(self, fake_redis_pair: tuple[object, object]) -> None:
        with pytest.raises(FileNotFoundError):
            redis_client.load_script("does_not_exist")

    def test_lua_dir_exists_and_contains_scripts(self) -> None:
        lua_dir = Path(redis_client.__file__).parent / "lua"
        names = {p.stem for p in lua_dir.glob("*.lua")}
        # The three canonical scripts from Modular_Design.md §8
        assert {
            "cleanup_position",
            "config_write_through",
            "capital_allocator_check_and_reserve",
        } <= names
