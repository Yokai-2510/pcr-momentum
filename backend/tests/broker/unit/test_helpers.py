"""Pure helpers from option_chain + option_contract."""

from __future__ import annotations

from datetime import date

from brokers.upstox.option_chain import strikes_around_atm, total_pcr
from brokers.upstox.option_contract import expiries_for, nearest_expiry, strikes_for


def _row(strike: int, ce_oi: int, pe_oi: int, spot: float = 23000.0) -> dict:
    return {
        "strike_price": strike,
        "underlying_spot_price": spot,
        "call_options": {"market_data": {"oi": ce_oi}},
        "put_options": {"market_data": {"oi": pe_oi}},
    }


def test_total_pcr_normal() -> None:
    chain = [_row(22900, 100, 200), _row(23000, 50, 50), _row(23100, 0, 100)]
    assert total_pcr(chain) == round(350 / 150, 4)


def test_total_pcr_zero_call_oi_returns_none() -> None:
    chain = [_row(23000, 0, 100), _row(23100, 0, 100)]
    assert total_pcr(chain) is None


def test_total_pcr_empty_returns_none() -> None:
    assert total_pcr([]) is None
    assert total_pcr(None) is None


def test_strikes_around_atm_centers_on_spot() -> None:
    chain = [_row(s, 1, 1, spot=23000.0) for s in (22900, 22950, 23000, 23050, 23100)]
    out = strikes_around_atm(chain, n_each_side=1)
    assert [r["strike_price"] for r in out] == [22950, 23000, 23050]


def test_strikes_around_atm_clamps_at_edges() -> None:
    chain = [_row(s, 1, 1, spot=22900.0) for s in (22900, 22950, 23000)]
    out = strikes_around_atm(chain, n_each_side=5)
    assert [r["strike_price"] for r in out] == [22900, 22950, 23000]


def test_strikes_around_atm_uses_explicit_spot() -> None:
    chain = [_row(s, 1, 1, spot=99999.0) for s in (22900, 23000, 23100)]
    out = strikes_around_atm(chain, spot=23000.0, n_each_side=0)
    assert [r["strike_price"] for r in out] == [23000]


def test_strikes_around_atm_empty() -> None:
    assert strikes_around_atm([]) == []
    assert strikes_around_atm(None) == []
    chain_no_spot = [{"strike_price": 100, "underlying_spot_price": None}]
    assert strikes_around_atm(chain_no_spot) == []


# ── option_contract helpers ────────────────────────────────────────────

CONTRACTS = [
    {
        "expiry": "2026-05-08",
        "instrument_type": "CE",
        "strike_price": 22900,
        "instrument_key": "k1",
    },
    {
        "expiry": "2026-05-08",
        "instrument_type": "PE",
        "strike_price": 22900,
        "instrument_key": "k2",
    },
    {
        "expiry": "2026-05-08",
        "instrument_type": "CE",
        "strike_price": 23000,
        "instrument_key": "k3",
    },
    {
        "expiry": "2026-05-15",
        "instrument_type": "CE",
        "strike_price": 23000,
        "instrument_key": "k4",
    },
]


def test_expiries_for_unique_sorted() -> None:
    assert expiries_for(CONTRACTS) == ["2026-05-08", "2026-05-15"]


def test_expiries_for_empty() -> None:
    assert expiries_for(None) == []
    assert expiries_for([]) == []


def test_nearest_expiry_picks_first_future() -> None:
    today = date(2026, 5, 7)
    assert nearest_expiry(CONTRACTS, today=today) == "2026-05-08"


def test_nearest_expiry_today_match_inclusive() -> None:
    today = date(2026, 5, 8)
    assert nearest_expiry(CONTRACTS, today=today) == "2026-05-08"


def test_nearest_expiry_no_future() -> None:
    today = date(2099, 1, 1)
    assert nearest_expiry(CONTRACTS, today=today) is None


def test_strikes_for_filters_by_expiry_and_type() -> None:
    out = strikes_for(CONTRACTS, "2026-05-08", "CE")
    assert [c["strike_price"] for c in out] == [22900, 23000]
    assert all(c["instrument_type"] == "CE" for c in out)


def test_strikes_for_unknown_returns_empty() -> None:
    assert strikes_for(CONTRACTS, "2099-01-01", "CE") == []
    assert strikes_for(None, "2026-05-08", "CE") == []
