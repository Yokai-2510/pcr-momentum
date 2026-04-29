"""
brokers.upstox.market_data — GET /v3/market-quote/ltp.

Last-traded price for ≤500 instrument keys per call. Returns a flat
{instrument_key: ltp_float} dict; entries with missing/zero LTP are dropped.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_headers
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_LTP_URL = "https://api.upstox.com/v3/market-quote/ltp"

MAX_KEYS_PER_CALL = 500


def get_ltp(
    instrument_keys: list[str],
    access_token: str,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    if not instrument_keys:
        return ok({}, code=0, raw=None)
    if len(instrument_keys) > MAX_KEYS_PER_CALL:
        return fail(f"too_many_keys: {len(instrument_keys)} > {MAX_KEYS_PER_CALL}")
    fetch_url = url or _LTP_URL
    params = {"instrument_key": ",".join(instrument_keys)}
    try:
        code, parsed, text, _ = _req(
            "GET",
            fetch_url,
            headers=bearer_headers(access_token, v=3),
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        result: dict[str, float] = {}
        for key, info in (parsed.get("data") or {}).items():
            ltp = (info or {}).get("last_price", 0.0)
            if ltp and ltp > 0:
                result[key] = float(ltp)
        return ok(result, code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
