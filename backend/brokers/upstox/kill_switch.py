"""
brokers.upstox.kill_switch — GET / POST /v2/user/kill-switch.

Per-segment trading halt. When enabled, all pending orders in that segment
are cancelled and new orders blocked. Atomic batch on POST.
"""

from __future__ import annotations

import json
from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_KILL_SWITCH_URL = "https://api.upstox.com/v2/user/kill-switch"


def get_kill_switch_status(
    access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    fetch_url = url or _KILL_SWITCH_URL
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_json(access_token, v=2), timeout=timeout
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


def set_kill_switch(
    toggles: list[dict[str, str]],
    access_token: str,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    fetch_url = url or _KILL_SWITCH_URL
    try:
        code, parsed, text, _ = _req(
            "POST",
            fetch_url,
            headers=bearer_json(access_token, v=2),
            data=json.dumps(toggles),
            timeout=timeout,
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


def is_segment_blocked(snapshot: list[dict[str, Any]] | None, segment: str = "NSE_FO") -> bool:
    """True if segment is INACTIVE OR has kill_switch_enabled. Unknown segment ⇒ blocked."""
    if not snapshot:
        return True
    for entry in snapshot:
        if entry.get("segment") == segment:
            if entry.get("segment_status") != "ACTIVE":
                return True
            return bool(entry.get("kill_switch_enabled"))
    return True
