"""Tests for the shared REST envelope builders."""

from __future__ import annotations

from brokers.upstox.envelopes import envelope, fail, ok


def test_envelope_shape() -> None:
    e = envelope(True, {"a": 1}, None, 200, {"raw": True})
    assert set(e.keys()) == {"success", "data", "error", "code", "raw"}
    assert e["success"] is True
    assert e["data"] == {"a": 1}
    assert e["code"] == 200


def test_ok_defaults() -> None:
    e = ok({"x": 1})
    assert e["success"] is True
    assert e["data"] == {"x": 1}
    assert e["code"] == 200
    assert e["error"] is None
    assert e["raw"] is None


def test_fail_carries_error_and_code() -> None:
    e = fail("MAINTENANCE_WINDOW", code=423)
    assert e["success"] is False
    assert e["error"] == "MAINTENANCE_WINDOW"
    assert e["code"] == 423
    assert e["data"] is None
