"""Tests for `state/config_loader.py` — Settings + runtime config helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from state import config_loader


class TestSettings:
    def test_settings_load_from_env(self, env_required: None) -> None:
        config_loader.reset_settings_cache()
        settings = config_loader.get_settings()
        assert settings.database_url.startswith("postgresql://")
        assert settings.redis_url.startswith("unix://")
        assert len(settings.jwt_secret) >= 32
        assert settings.app_env == "dev"

    def test_settings_cache(self, env_required: None) -> None:
        config_loader.reset_settings_cache()
        s1 = config_loader.get_settings()
        s2 = config_loader.get_settings()
        assert s1 is s2

    def test_missing_database_url_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Clear cache + the env var
        config_loader.reset_settings_cache()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # pydantic-settings may auto-load from .env; ensure no .env interferes
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValidationError):
            config_loader.get_settings()


class TestRuntimeConfigMap:
    def test_redis_key_for_known_section(self) -> None:
        assert (
            config_loader.redis_key_for_config("execution") == "strategy:configs:execution"
        )
        assert config_loader.redis_key_for_config("session") == "strategy:configs:session"
        assert config_loader.redis_key_for_config("risk") == "strategy:configs:risk"
        assert (
            config_loader.redis_key_for_config("index:nifty50")
            == "strategy:configs:indexes:nifty50"
        )

    def test_redis_key_for_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            config_loader.redis_key_for_config("garbage")


class _FakeConn:
    """Minimal stand-in for asyncpg.Connection.fetch."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        assert "config_settings" in sql
        return self._rows


class TestLoadRuntimeConfigs:
    async def test_parses_dict_value(self) -> None:
        conn = _FakeConn([{"key": "execution", "value": {"buffer_inr": 2}}])
        out = await config_loader.load_runtime_configs(conn)  # type: ignore[arg-type]
        assert out == {"execution": {"buffer_inr": 2}}

    async def test_parses_string_json_value(self) -> None:
        conn = _FakeConn(
            [{"key": "session", "value": json.dumps({"market_open": "09:15"})}]
        )
        out = await config_loader.load_runtime_configs(conn)  # type: ignore[arg-type]
        assert out["session"]["market_open"] == "09:15"

    async def test_unknown_value_type_raises(self) -> None:
        conn = _FakeConn([{"key": "risk", "value": 42}])
        with pytest.raises(TypeError):
            await config_loader.load_runtime_configs(conn)  # type: ignore[arg-type]
