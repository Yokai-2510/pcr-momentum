"""nifty50_universe — constituents parsing + universe map building."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from engines.init import nifty50_universe as u

CSV_SAMPLE = b"""Company Name,Industry,Symbol,Series,ISIN Code
Reliance Industries Ltd.,Oil & Gas,RELIANCE,EQ,INE002A01018
Tata Consultancy Services Ltd.,IT,TCS,EQ,INE467B01029
Mahindra & Mahindra Ltd.,Auto,M&M,EQ,INE101A01026
Dummy Corp,Test,DUMMYX,EQ,INE000000000
"""


def test_parse_constituents_csv_column_and_dummy_filter() -> None:
    symbols = u.parse_constituents_csv(CSV_SAMPLE)
    assert symbols == ["RELIANCE", "TCS", "M&M"]


def test_symbol_instrument_id_normalization() -> None:
    assert u.symbol_instrument_id("RELIANCE") == "stk_reliance"
    assert u.symbol_instrument_id("M&M") == "stk_m_m"
    assert u.symbol_instrument_id("BAJAJ-AUTO") == "stk_bajaj_auto"


def _master_rows() -> list[dict[str, Any]]:
    future_ms = int((datetime.now() + timedelta(days=20)).timestamp() * 1000)
    far_ms = int((datetime.now() + timedelta(days=48)).timestamp() * 1000)
    rows: list[dict[str, Any]] = [
        {
            "segment": "NSE_EQ",
            "trading_symbol": "RELIANCE",
            "instrument_key": "NSE_EQ|RELI",
            "last_price": 1000.0,
            "lot_size": 1,
        },
        # Noise: not in universe
        {
            "segment": "NSE_EQ",
            "trading_symbol": "OTHER",
            "instrument_key": "NSE_EQ|OTH",
            "last_price": 50.0,
        },
    ]
    # RELIANCE options: near + far expiry, strikes 900..1100 step 50
    for strike in range(900, 1101, 50):
        for side in ("CE", "PE"):
            rows.append(
                {
                    "segment": "NSE_FO",
                    "instrument_type": side,
                    "underlying_symbol": "RELIANCE",
                    "strike_price": strike,
                    "expiry": future_ms,
                    "instrument_key": f"NSE_FO|R{side}{strike}",
                    "lot_size": 250,
                }
            )
            rows.append(
                {
                    "segment": "NSE_FO",
                    "instrument_type": side,
                    "underlying_symbol": "RELIANCE",
                    "strike_price": strike,
                    "expiry": far_ms,
                    "instrument_key": f"NSE_FO|FAR{side}{strike}",
                    "lot_size": 250,
                }
            )
    return rows


def test_build_universe_maps() -> None:
    maps = u.build_universe_maps(_master_rows(), ["RELIANCE"], atm_strike_range=2)
    info = maps["symbols"]["RELIANCE"]
    assert info["instrument_id"] == "stk_reliance"
    assert info["spot_token"] == "NSE_EQ|RELI"
    assert info["lot_size"] == 250  # from the option contract
    assert info["prev_close"] == 1000.0

    chain = maps["chains"]["stk_reliance"]
    # ATM 1000 (from last_price), range ±2 * step 50 -> 900..1100, NEAREST expiry only
    assert sorted(int(s) for s in chain) == [900, 950, 1000, 1050, 1100]
    assert chain["1000"]["ce"]["token"] == "NSE_FO|RCE1000"
    assert "NSE_FO|FARCE1000" not in maps["token_map"]  # far expiry excluded

    tm = maps["token_map"]
    assert tm["NSE_EQ|RELI"]["side"] == "SPOT"
    assert tm["NSE_FO|RPE950"] == {
        "symbol": "RELIANCE",
        "instrument_id": "stk_reliance",
        "strike": 950,
        "side": "PE",
        "lot_size": 250,
    }


def test_build_skips_symbols_without_eq_row() -> None:
    maps = u.build_universe_maps(_master_rows(), ["RELIANCE", "MISSING"])
    assert "MISSING" not in maps["symbols"]
