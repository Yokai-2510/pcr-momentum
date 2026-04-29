"""
brokers.upstox._http — shared httpx helpers + header builders.

All Upstox REST endpoints flow through a single sync httpx.Client per call.
We do not pool a long-lived client because the broker SDK is stateless and
Phase-3 callers are not yet on the hot path; engines can later swap to
AsyncClient or a pooled Client without changing the public envelope.

Header builder maps:
    bearer_headers(v=2)  → Authorization + Accept (v2 default)
    bearer_headers(v=3)  → Authorization + Accept + Api-Version: 3.0
    bearer_json(v=2|3)   → adds Content-Type: application/json
    bearer_form()        → form-encoded variant for OAuth token exchange
"""

from __future__ import annotations

from typing import Any

import httpx


def bearer_headers(
    token: str | None, v: int = 2, content_type: str | None = None
) -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if v == 3:
        h["Api-Version"] = "3.0"
    if content_type:
        h["Content-Type"] = content_type
    return h


def bearer_json(token: str | None, v: int = 2) -> dict[str, str]:
    return bearer_headers(token, v=v, content_type="application/json")


def bearer_form(token: str | None = None) -> dict[str, str]:
    h = bearer_headers(token, v=2, content_type="application/x-www-form-urlencoded")
    return h


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    data: Any | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any] | list[Any] | None, str, bytes]:
    """
    Single shared synchronous round-trip helper.

    Returns: (status_code, parsed_json_or_None, text, raw_bytes).

    Catches transport-level errors and re-raises httpx.HTTPError so callers
    wrap to envelope("REQUEST_EXCEPTION: …"). Does NOT raise on non-200.
    """
    with httpx.Client(timeout=timeout) as client:
        r = client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            content=data if isinstance(data, (bytes, str)) else None,
            data=data if isinstance(data, dict) else None,
        )
    parsed: dict[str, Any] | list[Any] | None
    try:
        parsed = r.json() if r.content else None
    except Exception:
        parsed = None
    return r.status_code, parsed, r.text, r.content
