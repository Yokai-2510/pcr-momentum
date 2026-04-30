"""Entrypoint for `python -m engines.strategy`."""

from __future__ import annotations

import sys

from engines.strategy.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
