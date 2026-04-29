"""
brokers.upstox.capital - GET /v3/user/get-funds-and-margin.

HTTP 423 (Locked, daily 00:00-05:30 IST maintenance) is mapped to
`error="MAINTENANCE_WINDOW"` so callers can branch cleanly.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_headers
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_CAPITAL_URL = "https://api.upstox.com/v3/user/get-funds-and-margin"


def get_capital(access_token: str, timeout: int = 10, url: str | None = None) -> dict[str, Any]:
    fetch_url = url or _CAPITAL_URL
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_headers(access_token, v=3), timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 423:
        return fail("MAINTENANCE_WINDOW", code=423)
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(parsed.get("data"), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
