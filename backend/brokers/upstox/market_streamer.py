"""
brokers.upstox.market_streamer — thin wrapper over upstox_client.MarketDataStreamerV3.

Returns the LIVE SDK streamer object (not envelope-wrapped). The caller wires
on_message/on_error/etc. and decides what to do with each binary protobuf tick.

Modes: ltpc | full | option_greeks | full_d30
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    import upstox_client
except ImportError:  # pragma: no cover
    upstox_client = None

VALID_MODES = {"ltpc", "full", "option_greeks", "full_d30"}


def build_streamer(
    access_token: str,
    instrument_keys: list[str] | None = None,
    mode: str = "full",
    on_open: Callable[..., Any] | None = None,
    on_message: Callable[..., Any] | None = None,
    on_error: Callable[..., Any] | None = None,
    on_close: Callable[..., Any] | None = None,
    on_reconnecting: Callable[..., Any] | None = None,
    on_auto_reconnect_stop: Callable[..., Any] | None = None,
    auto_reconnect: bool = True,
    reconnect_interval_sec: int = 5,
    reconnect_max_retries: int = 10,
) -> Any:
    if upstox_client is None:
        raise RuntimeError("upstox-python-sdk is not installed")
    if mode not in VALID_MODES:
        raise ValueError(f"invalid_mode: {mode}")

    config = upstox_client.Configuration()
    config.access_token = access_token
    api_client = upstox_client.ApiClient(config)
    streamer = upstox_client.MarketDataStreamerV3(api_client, instrument_keys or [], mode)

    if on_open is not None:
        streamer.on("open", on_open)
    if on_message is not None:
        streamer.on("message", on_message)
    if on_error is not None:
        streamer.on("error", on_error)
    if on_close is not None:
        streamer.on("close", on_close)
    if on_reconnecting is not None:
        streamer.on("reconnecting", on_reconnecting)
    if on_auto_reconnect_stop is not None:
        streamer.on("autoReconnectStopped", on_auto_reconnect_stop)

    streamer.auto_reconnect(auto_reconnect, reconnect_interval_sec, reconnect_max_retries)
    return streamer


def start_streamer(
    access_token: str,
    instrument_keys: list[str],
    mode: str = "full",
    on_message: Callable[..., Any] | None = None,
    on_open: Callable[..., Any] | None = None,
    on_error: Callable[..., Any] | None = None,
    on_close: Callable[..., Any] | None = None,
    auto_reconnect: bool = True,
    reconnect_interval_sec: int = 5,
    reconnect_max_retries: int = 10,
) -> Any:
    streamer = build_streamer(
        access_token=access_token,
        instrument_keys=instrument_keys,
        mode=mode,
        on_open=on_open or (lambda: None),
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        auto_reconnect=auto_reconnect,
        reconnect_interval_sec=reconnect_interval_sec,
        reconnect_max_retries=reconnect_max_retries,
    )
    streamer.connect()
    return streamer
