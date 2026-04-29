"""
state.crypto — symmetric encryption for stored credentials.

Used by:
  - Init engine (postgres_hydrator) — decrypts user_credentials.encrypted_value
    and mirrors plaintext into Redis at boot.
  - FastAPI Gateway (Phase 9) — encrypts incoming POST /credentials/upstox
    payloads before INSERT.

Format on disk (BYTEA in user_credentials.encrypted_value):

    nonce(12) || ciphertext || tag(16)

  - AES-256-GCM, single static key from Settings.creds_encryption_key
  - The key is base64-encoded 32 raw bytes (44 chars) per
    state/config_loader.Settings; we decode once.
  - JSON serialization is plain UTF-8 json.dumps (not orjson) for stable
    sort + portability; encryption-roundtripping is the only consumer.

Threat model (single-user trading bot, dedicated EC2):
  - Protects credentials at rest in Postgres.
  - Does NOT protect a compromised host (the key is in .env on the same VM).
  - Rotation: re-encrypt + re-INSERT under the new key; no key-id field
    needed because we are single-user single-tenant.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12  # AES-GCM standard


def _decode_key(b64_key: str) -> bytes:
    """Decode the urlsafe-or-standard base64-encoded 32-byte AES-256 key.

    The setup tool that generates `CREDS_ENCRYPTION_KEY` may use either
    standard or urlsafe alphabet; tolerate both, but require 32 raw bytes.
    """
    key = b64_key.strip()
    try:
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    except Exception:
        raw = base64.b64decode(key + "=" * (-len(key) % 4))
    if len(raw) != 32:
        raise ValueError(f"CREDS_ENCRYPTION_KEY must decode to 32 bytes; got {len(raw)}")
    return raw


@lru_cache(maxsize=1)
def _aesgcm() -> AESGCM:
    """Return the process-wide AES-GCM cipher.

    Reads the key from `CREDS_ENCRYPTION_KEY` env var directly so this module
    has no import-time dependency on `state.config_loader.Settings` (which
    would create a hard-fail import for tests that don't set that var).
    """
    raw_key = os.getenv("CREDS_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raise RuntimeError("CREDS_ENCRYPTION_KEY not set")
    return AESGCM(_decode_key(raw_key))


def encrypt_json(payload: dict[str, Any]) -> bytes:
    """Encrypt a JSON-serialisable dict to a single BYTEA blob.

    Layout: nonce(12) || ciphertext || tag(16).
    """
    nonce = secrets.token_bytes(_NONCE_LEN)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ct = _aesgcm().encrypt(nonce, plaintext, associated_data=None)
    return nonce + ct


def decrypt_json(blob: bytes) -> dict[str, Any]:
    """Decrypt a blob produced by `encrypt_json` back into a dict.

    Raises:
        ValueError on malformed blob (too short).
        cryptography.exceptions.InvalidTag on auth failure.
    """
    if len(blob) <= _NONCE_LEN:
        raise ValueError(f"encrypted blob too short ({len(blob)} bytes)")
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    pt = _aesgcm().decrypt(nonce, ct, associated_data=None)
    out: dict[str, Any] = json.loads(pt.decode("utf-8"))
    return out


def reset_cache_for_testing() -> None:
    """Drop the cached AESGCM cipher so a new `CREDS_ENCRYPTION_KEY` env value
    takes effect mid-test."""
    _aesgcm.cache_clear()
