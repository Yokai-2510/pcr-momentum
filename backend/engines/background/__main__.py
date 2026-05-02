"""`python -m engines.background` shim."""

from __future__ import annotations

import sys

from engines.background.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
