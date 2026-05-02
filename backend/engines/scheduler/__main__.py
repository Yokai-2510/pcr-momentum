"""`python -m engines.scheduler` shim."""

from __future__ import annotations

import sys

from engines.scheduler.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
