"""
brokers.upstox.option_contract — GET /v2/option/contract.

Pure helpers (no I/O):
  expiries_for(contracts)                         — sorted unique expiry list
  nearest_expiry(contracts, today=None)           — closest expiry on/after today (IST)
  strikes_for(contracts, expiry, instrument_type) — CE/PE strikes for one expiry
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_OPTION_CONTRACT_URL = "https://api.upstox.com/v2/option/contract"

_IST = ZoneInfo("Asia/Kolkata")


def get_option_contracts(
    instrument_key: str,
    access_token: str,
    expiry_date: str | None = None,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    fetch_url = url or _OPTION_CONTRACT_URL
    params: dict[str, str] = {"instrument_key": instrument_key}
    if expiry_date:
        params["expiry_date"] = expiry_date
    try:
        code, parsed, text, _ = _req(
            "GET",
            fetch_url,
            headers=bearer_json(access_token, v=2),
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(parsed.get("data", []), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def expiries_for(contracts: list[dict[str, Any]] | None) -> list[str]:
    if not contracts:
        return []
    return sorted({str(c.get("expiry")) for c in contracts if c.get("expiry")})


def nearest_expiry(contracts: list[dict[str, Any]] | None, today: date | None = None) -> str | None:
    if not contracts:
        return None
    today = today or datetime.now(_IST).date()
    future: list[str] = []
    for exp in expiries_for(contracts):
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
            if d >= today:
                future.append(exp)
        except Exception:
            continue
    return future[0] if future else None


def strikes_for(
    contracts: list[dict[str, Any]] | None,
    expiry: str,
    instrument_type: str = "CE",
) -> list[dict[str, Any]]:
    if not contracts:
        return []
    out = [
        c
        for c in contracts
        if c.get("expiry") == expiry and c.get("instrument_type") == instrument_type
    ]
    return sorted(out, key=lambda x: x.get("strike_price") or 0)
