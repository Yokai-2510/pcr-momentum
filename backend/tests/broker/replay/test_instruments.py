"""Replay tests for instruments.download_master_contract — uses an in-memory
gzipped JSON fixture so we don't hit the CDN."""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import httpx
import respx

from brokers.upstox.instruments import download_master_contract


def _fake_master_gz(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as gz:
        json.dump(rows, gz)
    return buf.getvalue()


@respx.mock
def test_download_master_contract_writes_files(tmp_path: Path) -> None:
    rows = [
        {"instrument_key": "NSE_EQ|INE001", "segment": "NSE_EQ"},
        {"instrument_key": "NSE_FO|49520", "segment": "NSE_FO"},
        {"instrument_key": "NSE_INDEX|Nifty 50", "segment": "NSE_INDEX"},
    ]
    body = _fake_master_gz(rows)
    respx.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz").mock(
        return_value=httpx.Response(200, content=body)
    )

    res = download_master_contract(cache_dir=tmp_path)
    assert res["success"] is True
    d = res["data"]
    assert d["rows"] == 3
    assert set(d["segments"].keys()) == {"NSE_EQ", "NSE_FO", "NSE_INDEX"}
    assert (tmp_path / "master.json.gz").exists()
    assert (tmp_path / "master.json").exists()
    # Decompressed JSON survives round-trip
    with open(tmp_path / "master.json") as f:
        loaded = json.load(f)
    assert loaded == rows


@respx.mock
def test_download_master_contract_http_error(tmp_path: Path) -> None:
    respx.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz").mock(
        return_value=httpx.Response(503, content=b"")
    )
    res = download_master_contract(cache_dir=tmp_path)
    assert res["success"] is False
    assert res["code"] == 503
