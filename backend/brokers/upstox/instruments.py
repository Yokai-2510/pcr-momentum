"""
brokers.upstox.instruments — Upstox NSE master-contract download (CDN gz).

Endpoint:
  GET https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz

Decompresses to a JSON array (~100k+ rows: NSE_EQ + NSE_FO + INDEX). Caller
passes a `cache_dir` (Path) where both the .gz and decompressed .json are
written. The Init engine `instruments_loader` reads the .json.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from brokers.upstox.envelopes import envelope, fail

_MASTER_CONTRACT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def _build_headers(access_token: str | None) -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def download_master_contract(
    cache_dir: Path,
    access_token: str | None = None,
    timeout: int = 60,
    gz_filename: str = "master.json.gz",
    json_filename: str = "master.json",
    url: str | None = None,
) -> dict[str, Any]:
    fetch_url = url or _MASTER_CONTRACT_URL
    gz_path = cache_dir / gz_filename
    json_path = cache_dir / json_filename
    headers = _build_headers(access_token)

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=timeout) as client:
            r = client.get(fetch_url, headers=headers)
        if r.status_code != 200:
            return fail(f"HTTP {r.status_code} downloading master contract", code=r.status_code)

        with open(gz_path, "wb") as f:
            f.write(r.content)
        with gzip.open(gz_path, "rt", encoding="utf-8") as gz_file:
            rows = json.load(gz_file)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f)

        segments = dict(Counter(row.get("segment", "?") for row in rows))
        return envelope(
            True,
            {
                "gz_path": str(gz_path),
                "json_path": str(json_path),
                "bytes_gz": gz_path.stat().st_size,
                "bytes_json": json_path.stat().st_size,
                "rows": len(rows),
                "segments": segments,
            },
            None,
            r.status_code,
            None,
        )
    except Exception as e:
        return fail(f"DOWNLOAD_EXCEPTION: {e}")
