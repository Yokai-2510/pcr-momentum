"""Centralised logging configuration for every engine.

Per `Schema.md` §3, all logs are emitted as one-line JSON to stdout, where
journald captures them. In dev, a human-readable colour format is enabled
via the `APP_ENV=dev` environment variable.

Usage:

    from log_setup import configure
    configure("init")        # call once at engine startup
    from loguru import logger
    logger.info("event_processed", index="nifty50", duration_ms=12)
"""

from __future__ import annotations

import os
import sys
from typing import Final

from loguru import logger

_DEFAULT_LEVEL: Final[str] = "INFO"
_DEV_FORMAT: Final[str] = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{extra[engine]}</cyan>:<cyan>{module}</cyan> "
    "<level>{message}</level>"
)


def configure(engine_name: str, level: str | None = None) -> None:
    """Configure loguru for the calling engine.

    Args:
        engine_name: stable identifier emitted in every log line as `extra.engine`.
            One of: init, data_pipeline, strategy, order_exec, background,
            scheduler, health, api_gateway.
        level: log level name; defaults to env `LOG_LEVEL` or `INFO`.
    """
    chosen_level = (level or os.getenv("LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    is_dev = os.getenv("APP_ENV", "prod").lower() == "dev"

    logger.remove()

    if is_dev:
        logger.add(
            sys.stdout,
            level=chosen_level,
            format=_DEV_FORMAT,
            backtrace=False,
            diagnose=False,
            enqueue=False,
        )
    else:
        logger.add(
            sys.stdout,
            level=chosen_level,
            format="{message}",
            serialize=True,
            backtrace=False,
            diagnose=False,
            enqueue=False,
        )

    logger.configure(extra={"engine": engine_name})
