"""Configuration loader: `.env` (process env) + Postgres `config_settings`.

Two layers of configuration:

1. **Process / secret config** (`Settings`)
   Pulled from environment via `pydantic-settings`. These are the values
   every engine needs at startup: DSNs, secrets, app env, log level.
   Loaded once via `get_settings()`.

2. **Strategy / runtime config** (`load_runtime_configs`)
   Persisted in Postgres `config_settings` (Schema.md §2.2) and mirrored
   into Redis `strategy:configs:*` by Init at boot. This module exposes
   the canonical loader used by the Init engine and integration tests.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import asyncpg
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from state.keys import (
    STRATEGY_CONFIGS_EXECUTION,
    STRATEGY_CONFIGS_RISK,
    STRATEGY_CONFIGS_SESSION,
    strategy_config_index,
)


# ---------------------------------------------------------------------------
# Process settings (env-driven)
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """Process-level settings loaded from `.env` + os env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="prod", description="dev | prod")
    log_level: str = Field(default="INFO")

    # Datastores
    database_url: str = Field(
        ...,
        description="Postgres DSN, e.g. postgresql://trader:***@/premium_diff_bot",
    )
    redis_url: str = Field(
        default="unix:///var/run/redis/redis-server.sock",
        description="Either unix:///path or a TCP redis URL",
    )

    # Auth / security
    jwt_secret: str = Field(..., description="HMAC secret for JWT signing (>=32 bytes)")
    creds_encryption_key: str = Field(
        ..., description="AES-256-GCM key (44-char base64 of 32 raw bytes)"
    )

    # Optional bootstrap-only seeds (only consumed by Alembic seed migration)
    seed_admin_username: str | None = Field(default=None)
    seed_admin_password: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """Clear the cached `Settings` (used between tests)."""
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Runtime config (Postgres -> dict)
# ---------------------------------------------------------------------------

# Mapping `config_settings.key` -> Redis key it mirrors into.
# Aligns with Schema.md §1.4 (strategy:configs:*).
RUNTIME_CONFIG_REDIS_MAP: dict[str, str] = {
    "execution": STRATEGY_CONFIGS_EXECUTION,
    "session": STRATEGY_CONFIGS_SESSION,
    "risk": STRATEGY_CONFIGS_RISK,
    "index:nifty50": strategy_config_index("nifty50"),
    "index:banknifty": strategy_config_index("banknifty"),
}


async def load_runtime_configs(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
) -> dict[str, dict[str, Any]]:
    """Read all rows from `config_settings`, returning `{key: value_dict}`.

    `value` is JSONB in Postgres; asyncpg may return it as `str` or `dict`
    depending on codec setup. We coerce to `dict` for consistency.
    """
    sql = "SELECT key, value FROM config_settings"
    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            rows = await conn.fetch(sql)
    else:
        rows = await pool_or_conn.fetch(sql)

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row["value"]
        if isinstance(raw, str):
            parsed = json.loads(raw)
        elif isinstance(raw, dict):
            parsed = raw
        else:
            raise TypeError(
                f"config_settings.value must be JSON-decodable; got {type(raw).__name__}"
            )
        out[row["key"]] = parsed
    return out


def redis_key_for_config(config_key: str) -> str:
    """Return the Redis key that mirrors `config_settings.key`.

    Raises `KeyError` if `config_key` is not in the canonical mapping.
    """
    if config_key not in RUNTIME_CONFIG_REDIS_MAP:
        raise KeyError(
            f"unknown config key {config_key!r}; "
            f"expected one of {sorted(RUNTIME_CONFIG_REDIS_MAP)}"
        )
    return RUNTIME_CONFIG_REDIS_MAP[config_key]
