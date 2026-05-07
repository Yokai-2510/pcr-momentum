"""Unit + integration tests for ``credentials_store``.

Encryption tests use a deterministic test key so they run offline.
Postgres CRUD tests use a lightweight fake ``asyncpg.Connection``.
Redis sync tests use ``fakeredis``.
"""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from state.credentials_store import (
    _derive_key,
    bootstrap_from_file,
    clear_credentials,
    decrypt_blob,
    decrypt_json,
    delete_credentials,
    delete_from_redis,
    encrypt_blob,
    encrypt_json,
    hydrate_credentials_to_redis,
    init_engine_load_credentials,
    load_credentials,
    mask_credentials,
    mask_value,
    persist_credentials,
    read_credentials,
    read_local_credentials_file,
    reset_test_encryption_key,
    set_test_encryption_key,
    sync_to_redis,
    validate_upstox_payload,
    write_credentials,
)
from state.keys import USER_CREDENTIALS_UPSTOX


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _deterministic_key():
    """Use a fixed AES key for every test; reset afterwards."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    set_test_encryption_key(key)
    yield
    reset_test_encryption_key()


class _FakeConnection:
    """Minimal asyncpg.Connection stand-in for CRUD unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, bytes] = {}
        self._last_result: str = ""

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        broker = args[0]
        return SimpleNamespace(encrypted_value=self._rows.get(broker))

    async def execute(self, sql: str, *args: Any) -> str:
        broker = args[0]
        encrypted = args[1]
        if "DELETE" in sql:
            if broker in self._rows:
                del self._rows[broker]
                self._last_result = "DELETE 1"
            else:
                self._last_result = "DELETE 0"
            return self._last_result
        self._rows[broker] = encrypted
        self._last_result = "INSERT 0 1"
        return self._last_result


@pytest.fixture
def fake_conn():
    return _FakeConnection()


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

class TestDeriveKey:
    def test_urlsafe_b64(self) -> None:
        raw = os.urandom(32)
        b64 = base64.urlsafe_b64encode(raw).decode()
        assert _derive_key(b64) == raw

    def test_standard_b64(self) -> None:
        raw = os.urandom(32)
        b64 = base64.b64encode(raw).decode()
        assert _derive_key(b64) == raw

    def test_without_padding(self) -> None:
        raw = os.urandom(32)
        b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        assert _derive_key(b64) == raw

    def test_bad_length_raises(self) -> None:
        with pytest.raises(ValueError):
            _derive_key(base64.b64encode(os.urandom(16)).decode())

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            _derive_key("not-valid-base64!!!")


# ---------------------------------------------------------------------------
# Encryption round-trip
# ---------------------------------------------------------------------------

class TestEncryptDecrypt:
    def test_blob_round_trip(self) -> None:
        plain = b"sensitive-upstox-secret"
        blob = encrypt_blob(plain)
        assert decrypt_blob(blob) == plain

    def test_json_round_trip(self) -> None:
        data = {"api_key": "abc", "secret": "xyz"}
        blob = encrypt_json(data)
        assert decrypt_json(blob) == data

    def test_different_nonce_each_call(self) -> None:
        plain = b"same"
        b1 = encrypt_blob(plain)
        b2 = encrypt_blob(plain)
        assert b1[:12] != b2[:12]
        assert b1 != b2

    def test_tampered_blob_fails(self) -> None:
        blob = bytearray(encrypt_blob(b"original"))
        blob[-1] ^= 0xFF
        with pytest.raises(Exception):
            decrypt_blob(bytes(blob))


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

class TestMasking:
    def test_mask_value_none(self) -> None:
        assert mask_value(None) is None

    def test_mask_short_string(self) -> None:
        assert mask_value("abc") == "****"

    def test_mask_long_string(self) -> None:
        assert mask_value("verylongsecret", keep=4) == "****ongsecret"

    def test_mask_credentials(self) -> None:
        data = {
            "api_key": "ak-12345",
            "api_secret": "sec-67890",
            "totp_key": "FXJJFQBKGMP3E5X54S4FKOSW5LA6BOG2",
            "analytics_token": "tok",
            "sandbox_token": "sand",
            "pin": "1234",
            "mobile_no": "9310926729",
            "redirect_uri": "https://example.com",
        }
        masked = mask_credentials(data)
        assert masked["api_key"] == "****2345"
        assert masked["api_secret"] == "****7890"
        assert masked["totp_key"].startswith("****")
        assert masked["analytics_token"] == "****"
        assert masked["sandbox_token"] == "****"
        assert masked["pin"] == "****"
        assert masked["mobile_no"] == "*****926729"
        assert masked["redirect_uri"] == "https://example.com"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_complete_payload_passes(self) -> None:
        validate_upstox_payload(
            {
                "api_key": "a",
                "api_secret": "b",
                "redirect_uri": "c",
                "totp_key": "d",
                "mobile_no": "e",
                "pin": "f",
            }
        )

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValueError) as exc:
            validate_upstox_payload({"api_key": "a"})
        assert "missing required Upstox fields" in str(exc.value)


# ---------------------------------------------------------------------------
# Postgres CRUD (fake connection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPostgresCrud:
    async def test_read_missing_returns_none(self, fake_conn) -> None:
        assert await read_credentials(fake_conn, "upstox") is None

    async def test_write_then_read_round_trip(self, fake_conn) -> None:
        data = {"api_key": "k", "api_secret": "s"}
        await write_credentials(fake_conn, "upstox", data)
        result = await read_credentials(fake_conn, "upstox")
        assert result == data

    async def test_upsert_overwrites(self, fake_conn) -> None:
        await write_credentials(fake_conn, "upstox", {"api_key": "old"})
        await write_credentials(fake_conn, "upstox", {"api_key": "new"})
        assert (await read_credentials(fake_conn, "upstox")) == {"api_key": "new"}

    async def test_delete_existing(self, fake_conn) -> None:
        await write_credentials(fake_conn, "upstox", {"x": "y"})
        assert await delete_credentials(fake_conn, "upstox") is True
        assert await read_credentials(fake_conn, "upstox") is None

    async def test_delete_missing(self, fake_conn) -> None:
        assert await delete_credentials(fake_conn, "upstox") is False


# ---------------------------------------------------------------------------
# Redis sync (fakeredis)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRedisSync:
    async def test_sync_to_redis(self, fake_redis_async) -> None:
        r = fake_redis_async
        data = {"api_key": "k"}
        await sync_to_redis(r, "upstox", data)
        raw = await r.get(USER_CREDENTIALS_UPSTOX)
        assert json.loads(raw) == data

    async def test_delete_from_redis(self, fake_redis_async) -> None:
        r = fake_redis_async
        await r.set("user:credentials:dummy", "x")
        await delete_from_redis(r, "dummy")
        assert await r.exists("user:credentials:dummy") == 0


# ---------------------------------------------------------------------------
# Convenience round-trip (fake conn + fake redis)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestConvenience:
    async def test_persist_and_load(self, fake_conn, fake_redis_async) -> None:
        data = {"api_key": "persist"}
        await persist_credentials(fake_conn, fake_redis_async, "upstox", data)

        loaded = await load_credentials(fake_conn, fake_redis_async, "upstox")
        assert loaded == data
        raw = await fake_redis_async.get(USER_CREDENTIALS_UPSTOX)
        assert json.loads(raw) == data

    async def test_clear(self, fake_conn, fake_redis_async) -> None:
        await persist_credentials(fake_conn, fake_redis_async, "upstox", {"x": "y"})
        await clear_credentials(fake_conn, fake_redis_async, "upstox")
        assert await read_credentials(fake_conn, "upstox") is None
        assert await fake_redis_async.exists(USER_CREDENTIALS_UPSTOX) == 0


# ---------------------------------------------------------------------------
# Local file bootstrap
# ---------------------------------------------------------------------------

class TestLocalFile:
    def test_missing_file(self, tmp_path) -> None:
        assert read_local_credentials_file(str(tmp_path / "nope.json")) is None

    def test_reads_upstox(self, tmp_path) -> None:
        path = tmp_path / "creds.json"
        path.write_text(json.dumps({"upstox": {"api_key": "a"}}))
        assert read_local_credentials_file(str(path)) == {"api_key": "a"}


@pytest.mark.asyncio
class TestBootstrapFromFile:
    async def test_imports_and_validates(self, fake_conn, fake_redis_async, tmp_path) -> None:
        path = tmp_path / "creds.json"
        payload = {
            "api_key": "a",
            "api_secret": "b",
            "redirect_uri": "c",
            "totp_key": "d",
            "mobile_no": "e",
            "pin": "f",
        }
        path.write_text(json.dumps({"upstox": payload}))
        result = await bootstrap_from_file(fake_conn, fake_redis_async, str(path))
        assert result is True
        assert await read_credentials(fake_conn, "upstox") == payload

    async def test_missing_file(self, fake_conn, fake_redis_async, tmp_path) -> None:
        result = await bootstrap_from_file(
            fake_conn, fake_redis_async, str(tmp_path / "nope.json")
        )
        assert result is False


# ---------------------------------------------------------------------------
# Init-engine helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestInitEngineHelpers:
    async def test_hydrate_success(self, fake_conn, fake_redis_async) -> None:
        await write_credentials(fake_conn, "upstox", {"k": "v"})
        data = await hydrate_credentials_to_redis(fake_conn, fake_redis_async)
        assert data == {"k": "v"}
        assert json.loads(await fake_redis_async.get(USER_CREDENTIALS_UPSTOX)) == {"k": "v"}

    async def test_hydrate_missing(self, fake_conn, fake_redis_async) -> None:
        assert await hydrate_credentials_to_redis(fake_conn, fake_redis_async) is None

    async def test_init_load_success(self, fake_conn, fake_redis_async) -> None:
        await write_credentials(fake_conn, "upstox", {"api_key": "a"})
        data, reason = await init_engine_load_credentials(fake_conn, fake_redis_async)
        assert data == {"api_key": "a"}
        assert reason is None
        assert json.loads(await fake_redis_async.get(USER_CREDENTIALS_UPSTOX)) == {"api_key": "a"}

    async def test_init_load_missing(self, fake_conn, fake_redis_async) -> None:
        data, reason = await init_engine_load_credentials(fake_conn, fake_redis_async)
        assert data is None
        assert reason == "missing"

    async def test_init_load_decrypt_fails(self, fake_conn, fake_redis_async) -> None:
        fake_conn._rows["upstox"] = b"corrupted"
        data, reason = await init_engine_load_credentials(fake_conn, fake_redis_async)
        assert data is None
        assert reason == "decrypt_failed"


# ---------------------------------------------------------------------------
# Integration: real Postgres pool (gated)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPostgresIntegration:
    @pytest.mark.integration
    async def test_real_pool_round_trip(self, integration_db_url) -> None:
        if not integration_db_url:
            pytest.skip("DATABASE_URL not set")

        import asyncpg

        pool = await asyncpg.create_pool(integration_db_url)
        assert pool is not None
        try:
            # Ensure table exists (migration may not have run in unit-test DB)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_credentials (
                        broker TEXT PRIMARY KEY,
                        encrypted_value BYTEA NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
            data = {"api_key": "int-key", "api_secret": "int-secret"}
            await write_credentials(pool, "upstox", data)
            result = await read_credentials(pool, "upstox")
            assert result == data
            assert await delete_credentials(pool, "upstox") is True
            assert await read_credentials(pool, "upstox") is None
        finally:
            await pool.close()
