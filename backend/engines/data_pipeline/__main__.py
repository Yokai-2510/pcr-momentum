"""python -m engines.data_pipeline → engines.data_pipeline.main."""

from __future__ import annotations

import sys

from engines.data_pipeline.main import _entrypoint

if __name__ == "__main__":
    sys.exit(_entrypoint())
