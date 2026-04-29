"""Entrypoint for `python -m engines.init`."""

from __future__ import annotations

import sys

from engines.init.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
