"""`python -m engines.health` shim."""

from __future__ import annotations

import sys

from engines.health.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
