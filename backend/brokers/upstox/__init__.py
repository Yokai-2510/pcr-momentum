"""
brokers.upstox — Upstox broker layer for premium_diff_bot.

Recommended use:
    from brokers.upstox import UpstoxAPI

    res = UpstoxAPI.get_capital({"access_token": tok})

UpstoxAPI is stateless: pass `access_token` (and any other params) inside
the `params` dict on every call. All REST methods return the standard
envelope: {success, data, error, code, raw}. Streamer methods return the
live SDK streamer object directly.

Tests / debugging may import individual modules directly:

    from brokers.upstox.option_chain import total_pcr
    from brokers.upstox.holidays import is_holiday_for
"""

from brokers.upstox.client import UpstoxAPI

__all__ = ["UpstoxAPI"]
