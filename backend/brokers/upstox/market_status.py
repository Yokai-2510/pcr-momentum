"""
brokers.upstox.market_status — GET /v2/market/status/{exchange}.

Real-time exchange status. Predicates:
  is_open(status)     — NORMAL_OPEN | SPECIAL_OPEN
  is_pre_open(status) — PRE_OPEN_START | PRE_OPEN_END
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_MARKET_STATUS_URL_TPL = "https://api.upstox.com/v2/market/status/{exchange}"

_IST = ZoneInfo("Asia/Kolkata")

OPEN_STATUSES = {"NORMAL_OPEN", "SPECIAL_OPEN"}
PRE_OPEN_STATUSES = {"PRE_OPEN_START", "PRE_OPEN_END"}
CLOSED_STATUSES = {"NORMAL_CLOSE", "SPECIAL_CLOSE", "CLOSING_END"}


def _epoch_ms_to_ist(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000.0, tz=_IST).isoformat(timespec="seconds")
    except Exception:
        return None


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    status = data.get("status") or ""
    return {
        "exchange": data.get("exchange"),
        "status": status,
        "is_open": status in OPEN_STATUSES,
        "is_pre_open": status in PRE_OPEN_STATUSES,
        "is_closed": status in CLOSED_STATUSES,
        "last_updated_ist": _epoch_ms_to_ist(data.get("last_updated")),
        "last_updated_epoch_ms": data.get("last_updated"),
    }


def get_market_status(
    exchange: str,
    access_token: str,
    timeout: int = 10,
    url_tpl: str | None = None,
) -> dict[str, Any]:
    tpl = url_tpl or _MARKET_STATUS_URL_TPL
    fetch_url = tpl.format(exchange=exchange)
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_json(access_token, v=2), timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(_normalize(parsed.get("data") or {}), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def is_open(status: str | None) -> bool:
    return bool(status) and status in OPEN_STATUSES


def is_pre_open(status: str | None) -> bool:
    return bool(status) and status in PRE_OPEN_STATUSES
