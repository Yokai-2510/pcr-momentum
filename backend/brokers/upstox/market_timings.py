"""
brokers.upstox.market_timings - GET /v2/market/timings/{date}.

Per-exchange open/close for one date. `is_standard_session` confirms the
NSE entry is exactly the standard 09:15-15:30 IST window - anything else
(SPECIAL_TIMING, muhurat, DR session) returns False.
"""

from __future__ import annotations

from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_MARKET_TIMINGS_URL_TPL = "https://api.upstox.com/v2/market/timings/{date}"

_IST = ZoneInfo("Asia/Kolkata")

DEFAULT_STANDARD_OPEN = dt_time(9, 15, 0)
DEFAULT_STANDARD_CLOSE = dt_time(15, 30, 0)


def _epoch_ms_to_ist_dt(epoch_ms: int | None) -> datetime | None:
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000.0, tz=_IST)
    except Exception:
        return None


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    start_dt = _epoch_ms_to_ist_dt(entry.get("start_time"))
    end_dt = _epoch_ms_to_ist_dt(entry.get("end_time"))
    return {
        "exchange": entry.get("exchange"),
        "start_time_ist": start_dt.isoformat(timespec="seconds") if start_dt else None,
        "end_time_ist": end_dt.isoformat(timespec="seconds") if end_dt else None,
        "start_hhmm": start_dt.strftime("%H:%M") if start_dt else None,
        "end_hhmm": end_dt.strftime("%H:%M") if end_dt else None,
        "start_epoch_ms": entry.get("start_time"),
        "end_epoch_ms": entry.get("end_time"),
    }


def get_market_timings(
    date: str,
    access_token: str,
    timeout: int = 10,
    url_tpl: str | None = None,
) -> dict[str, Any]:
    tpl = url_tpl or _MARKET_TIMINGS_URL_TPL
    fetch_url = tpl.format(date=date)
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_json(access_token, v=2), timeout=timeout
        )
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


def get_timings_for(
    entries: list[dict[str, Any]] | None, exchange: str = "NSE"
) -> dict[str, Any] | None:
    if not entries:
        return None
    for e in entries:
        if e.get("exchange") == exchange:
            return e
    return None


def is_standard_session(
    entries: list[dict[str, Any]] | None,
    exchange: str = "NSE",
    expected_open: dt_time = DEFAULT_STANDARD_OPEN,
    expected_close: dt_time = DEFAULT_STANDARD_CLOSE,
) -> bool:
    entry = get_timings_for(entries, exchange)
    if not entry:
        return False
    if not entry.get("start_hhmm") or not entry.get("end_hhmm"):
        return False
    try:
        s_h, s_m = (int(x) for x in entry["start_hhmm"].split(":"))
        e_h, e_m = (int(x) for x in entry["end_hhmm"].split(":"))
        return dt_time(s_h, s_m) == expected_open and dt_time(e_h, e_m) == expected_close
    except Exception:
        return False
