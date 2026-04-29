"""brokers.upstox.positions — GET /v2/portfolio/short-term-positions."""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_headers
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_POSITIONS_URL = "https://api.upstox.com/v2/portfolio/short-term-positions"


def get_positions(access_token: str, timeout: int = 10, url: str | None = None) -> dict[str, Any]:
    fetch_url = url or _POSITIONS_URL
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_headers(access_token, v=2), timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(parsed.get("data") or [], code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
