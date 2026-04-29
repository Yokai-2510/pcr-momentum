"""
brokers.upstox.static_ips — GET / PUT /v2/user/ip.

WARNING: a successful PUT INVALIDATES all access tokens and is rate-limited
to once per calendar week. Tests should never call `update_static_ips` live.
"""

from __future__ import annotations

import json
from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_STATIC_IPS_URL = "https://api.upstox.com/v2/user/ip"


def get_static_ips(access_token: str, timeout: int = 10, url: str | None = None) -> dict[str, Any]:
    fetch_url = url or _STATIC_IPS_URL
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_json(access_token, v=2), timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(parsed.get("data"), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def update_static_ips(
    primary_ip: str,
    access_token: str,
    secondary_ip: str | None = None,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    body: dict[str, str] = {"primary_ip": primary_ip}
    if secondary_ip:
        body["secondary_ip"] = secondary_ip
    fetch_url = url or _STATIC_IPS_URL
    try:
        code, parsed, text, _ = _req(
            "PUT",
            fetch_url,
            headers=bearer_json(access_token, v=2),
            data=json.dumps(body),
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        d = parsed.get("data") or {}
        return ok(
            {
                "primary_ip": d.get("primary_ip"),
                "secondary_ip": d.get("secondary_ip"),
                "primary_ip_updated_at": d.get("primary_ip_updated_at"),
                "secondary_ip_updated_at": d.get("secondary_ip_updated_at"),
                "tokens_invalidated": bool(d.get("access_tokens_invalidated", False)),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
