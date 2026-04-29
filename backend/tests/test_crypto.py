"""state.crypto — round-trip + tamper detection."""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from state import crypto


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"\x01" * 32
    monkeypatch.setenv("CREDS_ENCRYPTION_KEY", base64.urlsafe_b64encode(raw).decode())
    crypto.reset_cache_for_testing()


def test_round_trip() -> None:
    payload = {"api_key": "k", "api_secret": "s", "totp_key": "T", "pin": "180225"}
    blob = crypto.encrypt_json(payload)
    assert crypto.decrypt_json(blob) == payload


def test_blob_starts_with_random_nonce() -> None:
    a = crypto.encrypt_json({"x": 1})
    b = crypto.encrypt_json({"x": 1})
    # Same plaintext under different nonces ⇒ different ciphertexts
    assert a != b
    # Both decrypt back to identity
    assert crypto.decrypt_json(a) == {"x": 1}
    assert crypto.decrypt_json(b) == {"x": 1}


def test_tamper_raises() -> None:
    blob = crypto.encrypt_json({"x": 1})
    tampered = blob[:-1] + bytes([(blob[-1] ^ 0x01)])
    with pytest.raises(InvalidTag):
        crypto.decrypt_json(tampered)


def test_short_blob_raises() -> None:
    with pytest.raises(ValueError):
        crypto.decrypt_json(b"too short")


def test_missing_key_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDS_ENCRYPTION_KEY", raising=False)
    crypto.reset_cache_for_testing()
    with pytest.raises(RuntimeError):
        crypto.encrypt_json({"x": 1})


def test_bad_key_length_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"\x00" * 16).decode())
    crypto.reset_cache_for_testing()
    with pytest.raises(ValueError):
        crypto.encrypt_json({"x": 1})
