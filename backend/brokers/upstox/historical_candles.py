"""
brokers.upstox.historical_candles — GET /v3/historical-candle/...

Custom unit + interval candles, returned as a list of 7-tuples
[ts_ist, open, high, low, close, volume, oi]. We add a `rows` view with
each candle as a labelled dict for ergonomic access.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_CANDLE_URL_TPL = "https://api.upstox.com/v3/historical-candle/{key}/{unit}/{interval}/{to_date}"
_CANDLE_URL_RANGE_TPL = (
    "https://api.upstox.com/v3/historical-candle/{key}/{unit}/{interval}/{to_date}/{from_date}"
)
_INTRADAY_URL_TPL = "https://api.upstox.com/v3/historical-candle/intraday/{key}/{unit}/{interval}"

VALID_UNITS = {"minutes", "hours", "days", "weeks", "months"}


def _normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    candles = (parsed.get("data") or {}).get("candles") or []
    rows = []
    for row in candles:
        if not isinstance(row, list) or len(row) < 7:
            continue
        rows.append(
            {
                "ts_ist": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
                "oi": row[6],
            }
        )
    return {"candles": candles, "rows": rows}


def _do_fetch(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        code, parsed, text, _ = _req("GET", url, headers=headers, timeout=timeout)
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(_normalize(parsed), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def get_historical_candles(
    instrument_key: str,
    unit: str,
    interval: int,
    to_date: str,
    access_token: str,
    from_date: str | None = None,
    timeout: int = 15,
    url_tpl: str | None = None,
    url_range_tpl: str | None = None,
) -> dict[str, Any]:
    if unit not in VALID_UNITS:
        return fail(f"invalid_unit: {unit}")
    if from_date:
        tpl = url_range_tpl or _CANDLE_URL_RANGE_TPL
        url = tpl.format(
            key=instrument_key,
            unit=unit,
            interval=interval,
            to_date=to_date,
            from_date=from_date,
        )
    else:
        tpl = url_tpl or _CANDLE_URL_TPL
        url = tpl.format(key=instrument_key, unit=unit, interval=interval, to_date=to_date)
    return _do_fetch(url, bearer_json(access_token, v=3), timeout)


def get_intraday_candles(
    instrument_key: str,
    unit: str,
    interval: int,
    access_token: str,
    timeout: int = 10,
    url_tpl: str | None = None,
) -> dict[str, Any]:
    if unit not in VALID_UNITS:
        return fail(f"invalid_unit: {unit}")
    tpl = url_tpl or _INTRADAY_URL_TPL
    url = tpl.format(key=instrument_key, unit=unit, interval=interval)
    return _do_fetch(url, bearer_json(access_token, v=3), timeout)
