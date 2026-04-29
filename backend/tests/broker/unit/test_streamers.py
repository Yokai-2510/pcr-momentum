"""Unit tests for streamer builders — uses monkey-patched fake SDK so we
don't make a real WebSocket connection."""

from __future__ import annotations

from typing import Any

import pytest

from brokers.upstox import market_streamer, portfolio_streamer


class _FakeStreamer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.events: dict[str, Any] = {}
        self.connected = False
        self.reconnect_cfg: tuple[bool, int, int] | None = None

    def on(self, event: str, handler: Any) -> None:
        self.events[event] = handler

    def auto_reconnect(self, enabled: bool, interval: int, retries: int) -> None:
        self.reconnect_cfg = (enabled, interval, retries)

    def connect(self) -> None:
        self.connected = True


class _FakeConfig:
    def __init__(self) -> None:
        self.access_token: str | None = None


class _FakeApiClient:
    def __init__(self, config: Any) -> None:
        self.config = config


class _FakeSDK:
    Configuration = _FakeConfig
    ApiClient = _FakeApiClient
    MarketDataStreamerV3 = _FakeStreamer
    PortfolioDataStreamer = _FakeStreamer


def _patch_sdk(monkeypatch, target_module) -> None:
    monkeypatch.setattr(target_module, "upstox_client", _FakeSDK)


def test_market_streamer_build_wires_handlers(monkeypatch) -> None:
    _patch_sdk(monkeypatch, market_streamer)
    seen = []

    def on_message(msg):
        seen.append(msg)

    s = market_streamer.build_streamer(
        access_token="t",
        instrument_keys=["NSE_INDEX|Nifty 50"],
        mode="ltpc",
        on_message=on_message,
    )
    assert isinstance(s, _FakeStreamer)
    assert s.connected is False
    assert "message" in s.events
    assert s.reconnect_cfg == (True, 5, 10)


def test_market_streamer_invalid_mode(monkeypatch) -> None:
    _patch_sdk(monkeypatch, market_streamer)
    with pytest.raises(ValueError):
        market_streamer.build_streamer(access_token="t", mode="weird")


def test_market_streamer_start_connects(monkeypatch) -> None:
    _patch_sdk(monkeypatch, market_streamer)
    s = market_streamer.start_streamer(
        access_token="t", instrument_keys=["NSE_INDEX|Nifty 50"], mode="full"
    )
    assert s.connected is True


def test_market_streamer_missing_sdk_raises(monkeypatch) -> None:
    monkeypatch.setattr(market_streamer, "upstox_client", None)
    with pytest.raises(RuntimeError):
        market_streamer.build_streamer(access_token="t")


def test_portfolio_streamer_build(monkeypatch) -> None:
    _patch_sdk(monkeypatch, portfolio_streamer)
    s = portfolio_streamer.build_streamer(
        access_token="t",
        order_update=True,
        position_update=True,
    )
    assert isinstance(s, _FakeStreamer)
    assert s.connected is False
    assert s.reconnect_cfg == (True, 5, 10)


def test_portfolio_streamer_start_connects(monkeypatch) -> None:
    _patch_sdk(monkeypatch, portfolio_streamer)
    s = portfolio_streamer.start_streamer(access_token="t")
    assert s.connected is True


def test_portfolio_streamer_missing_sdk_raises(monkeypatch) -> None:
    monkeypatch.setattr(portfolio_streamer, "upstox_client", None)
    with pytest.raises(RuntimeError):
        portfolio_streamer.build_streamer(access_token="t")
