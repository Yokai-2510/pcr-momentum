"""
brokers.upstox.holidays — Market holidays (current year + per-date probe).

Endpoints:
  GET /v2/market/holidays            full-year list
  GET /v2/market/holidays/{date}     per-date probe (empty list = not a holiday)

Predicate `is_holiday_for(entries, exchange)` is fail-closed: if the
exchange is in closed_exchanges, fully closed, or absent from open_exchanges,
returns True (= treat as holiday).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_headers
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_HOLIDAYS_URL = "https://api.upstox.com/v2/market/holidays"
_HOLIDAY_BY_DATE_TPL = "https://api.upstox.com/v2/market/holidays/{date}"

_IST = ZoneInfo("Asia/Kolkata")


def _epoch_ms_to_ist(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000.0, tz=_IST).isoformat(timespec="seconds")
    except Exception:
        return None


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    open_exchanges_raw = entry.get("open_exchanges", []) or []
    open_exchanges = [
        {
            "exchange": x.get("exchange"),
            "start_time_ist": _epoch_ms_to_ist(x.get("start_time")),
            "end_time_ist": _epoch_ms_to_ist(x.get("end_time")),
            "start_epoch_ms": x.get("start_time"),
            "end_epoch_ms": x.get("end_time"),
        }
        for x in open_exchanges_raw
    ]
    return {
        "date": entry.get("date"),
        "description": entry.get("description"),
        "type": entry.get("holiday_type"),
        "closed_exchanges": entry.get("closed_exchanges", []) or [],
        "open_exchanges": open_exchanges,
        "is_fully_closed": len(open_exchanges) == 0,
    }


def _fetch_normalized(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        code, parsed, text, _ = _req("GET", url, headers=headers, timeout=timeout)
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        entries = [_normalize_entry(x) for x in parsed.get("data", [])]
        return ok(entries, code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def get_holidays(
    access_token: str | None = None, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    return _fetch_normalized(url or _HOLIDAYS_URL, bearer_headers(access_token, v=2), timeout)


def get_holiday_by_date(
    date: str,
    access_token: str | None = None,
    timeout: int = 10,
    url_tpl: str | None = None,
) -> dict[str, Any]:
    tpl = url_tpl or _HOLIDAY_BY_DATE_TPL
    return _fetch_normalized(tpl.format(date=date), bearer_headers(access_token, v=2), timeout)


def is_holiday_for(entries: list[dict[str, Any]] | None, exchange: str = "NSE") -> bool:
    if not entries:
        return False
    for e in entries:
        if exchange in (e.get("closed_exchanges") or []):
            return True
        opens = e.get("open_exchanges") or []
        if not opens:
            return True
        listed = {x.get("exchange") for x in opens}
        if exchange not in listed:
            return True
    return False
