"""Entrypoint for `python -m engines.order_exec`."""

from __future__ import annotations

import sys

from engines.order_exec.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
