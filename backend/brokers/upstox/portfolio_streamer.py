"""
brokers.upstox.portfolio_streamer — thin wrapper over upstox_client.PortfolioDataStreamer.

Real-time order / position / holding / GTT updates. Caller wires events;
no message processing here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    import upstox_client
except ImportError:  # pragma: no cover
    upstox_client = None


def build_streamer(
    access_token: str,
    order_update: bool = True,
    position_update: bool = False,
    holding_update: bool = False,
    gtt_update: bool = False,
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

    config = upstox_client.Configuration()
    config.access_token = access_token
    api_client = upstox_client.ApiClient(config)
    streamer = upstox_client.PortfolioDataStreamer(
        api_client,
        order_update=order_update,
        position_update=position_update,
        holding_update=holding_update,
        gtt_update=gtt_update,
    )

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
    order_update: bool = True,
    position_update: bool = False,
    holding_update: bool = False,
    gtt_update: bool = False,
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
        order_update=order_update,
        position_update=position_update,
        holding_update=holding_update,
        gtt_update=gtt_update,
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
