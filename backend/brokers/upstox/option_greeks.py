"""
brokers.upstox.option_greeks — GET /v3/market-quote/option-greek (≤50 keys).

Returned data is rekeyed by `instrument_token` (e.g. "NSE_FO|43885") so
callers can look up by the same identifier they pass in.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_OPTION_GREEK_URL = "https://api.upstox.com/v3/market-quote/option-greek"

MAX_KEYS_PER_CALL = 50


def get_option_greeks(
    instrument_keys: list[str],
    access_token: str,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    if not instrument_keys:
        return ok({}, code=0, raw=None)
    if len(instrument_keys) > MAX_KEYS_PER_CALL:
        return fail(f"too_many_keys: {len(instrument_keys)} > {MAX_KEYS_PER_CALL}")
    fetch_url = url or _OPTION_GREEK_URL
    params = {"instrument_key": ",".join(instrument_keys)}
    try:
        code, parsed, text, _ = _req(
            "GET",
            fetch_url,
            headers=bearer_json(access_token, v=3),
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        rekeyed: dict[str, dict[str, Any]] = {}
        for _, info in (parsed.get("data") or {}).items():
            tok = (info or {}).get("instrument_token")
            if tok:
                rekeyed[tok] = info
        return ok(rekeyed, code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
