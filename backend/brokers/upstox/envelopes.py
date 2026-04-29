"""
brokers.upstox.envelopes — single source of truth for the REST envelope.

Every REST classmethod on UpstoxAPI returns this exact shape:

    { "success": bool, "data": Any, "error": str | None,
      "code": int | None, "raw": dict[str, Any] | None }

`success` is True iff the underlying HTTP call returned 200 AND the broker
status field was "success" (where applicable). `error` carries a short
machine-friendly token like "MAINTENANCE_WINDOW", "REQUEST_EXCEPTION: …",
or "HTTP <code>: <body>"; `raw` carries the untouched broker JSON for
forensic inspection.
"""

from __future__ import annotations

from typing import Any


def envelope(
    success: bool,
    data: Any,
    error: str | None,
    code: int | None,
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "success": success,
        "data": data,
        "error": error,
        "code": code,
        "raw": raw,
    }


def ok(data: Any, code: int = 200, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return envelope(True, data, None, code, raw)


def fail(error: str, code: int | None = None, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return envelope(False, None, error, code, raw)
