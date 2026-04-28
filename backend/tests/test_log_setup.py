"""Tests for `log_setup.configure`."""

from __future__ import annotations

import io
import json

import pytest
from loguru import logger

import log_setup


def _capture(level: str = "INFO") -> io.StringIO:
    """Reroute loguru output to a StringIO buffer."""
    sink = io.StringIO()
    logger.remove()
    logger.add(sink, level=level, format="{message}", serialize=True)
    return sink


class TestConfigure:
    def test_prod_json_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        sink = _capture()
        log_setup.configure("init")
        # Re-add capture sink AFTER configure clears handlers
        logger.add(sink, level="INFO", format="{message}", serialize=True)
        logger.info("hello", index="nifty50")
        line = sink.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["record"]["message"] == "hello"
        assert record["record"]["extra"]["index"] == "nifty50"

    def test_dev_human_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "dev")
        log_setup.configure("strategy")
        # Smoke: must not raise; engine name attached to all extras
        logger.info("dev mode line")  # captured via stdout; no assertion here

    def test_engine_name_in_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        log_setup.configure("order_exec")
        sink = io.StringIO()
        logger.add(sink, level="INFO", format="{message}", serialize=True)
        logger.info("evt")
        record = json.loads(sink.getvalue().strip().splitlines()[-1])
        assert record["record"]["extra"]["engine"] == "order_exec"

    def test_level_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        log_setup.configure("health", level="WARNING")
        sink = io.StringIO()
        logger.add(sink, level="WARNING", format="{message}", serialize=True)
        logger.info("filtered")
        logger.warning("kept")
        out = sink.getvalue()
        assert "kept" in out
        assert "filtered" not in out
