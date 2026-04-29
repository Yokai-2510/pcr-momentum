"""init.strike_basket_builder — pure helpers."""

from __future__ import annotations

from datetime import date

from engines.init.strike_basket_builder import (
    build_option_chain_template,
    build_trading_basket,
    compute_atm,
    discover_nearest_expiry,
    filter_atm_window_strikes,
)

# ── compute_atm ───────────────────────────────────────────────────────


def test_compute_atm_rounds_nearest() -> None:
    assert compute_atm(23012.0, 50) == 23000
    assert compute_atm(23026.0, 50) == 23050
    assert compute_atm(23025.0, 50) == 23000  # banker's rounding via round()


def test_compute_atm_banknifty_step_100() -> None:
    assert compute_atm(50049.0, 100) == 50000
    assert compute_atm(50051.0, 100) == 50100


def test_compute_atm_invalid_step_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        compute_atm(23000.0, 0)


# ── discover_nearest_expiry ───────────────────────────────────────────

CONTRACTS = [
    {"expiry": "2026-04-30", "instrument_type": "CE", "strike_price": 22900},
    {"expiry": "2026-05-08", "instrument_type": "CE", "strike_price": 23000},
    {"expiry": "2026-05-15", "instrument_type": "CE", "strike_price": 23000},
]


def test_nearest_expiry_picks_first_future() -> None:
    assert discover_nearest_expiry(CONTRACTS, date(2026, 4, 29)) == "2026-04-30"


def test_nearest_expiry_today_inclusive() -> None:
    assert discover_nearest_expiry(CONTRACTS, date(2026, 4, 30)) == "2026-04-30"


def test_nearest_expiry_skips_past() -> None:
    assert discover_nearest_expiry(CONTRACTS, date(2026, 5, 1)) == "2026-05-08"


def test_nearest_expiry_none_in_future() -> None:
    assert discover_nearest_expiry(CONTRACTS, date(2099, 1, 1)) is None


def test_nearest_expiry_empty_list() -> None:
    assert discover_nearest_expiry([], date(2026, 4, 29)) is None


# ── filter_atm_window_strikes ─────────────────────────────────────────


def _expiry_set(strikes: list[int], expiry: str) -> list[dict]:
    out = []
    for s in strikes:
        out.append(
            {
                "expiry": expiry,
                "instrument_type": "CE",
                "strike_price": s,
                "instrument_key": f"CE_{s}",
            }
        )
        out.append(
            {
                "expiry": expiry,
                "instrument_type": "PE",
                "strike_price": s,
                "instrument_key": f"PE_{s}",
            }
        )
    return out


def test_filter_window_returns_strikes_and_contracts() -> None:
    contracts = _expiry_set(
        [22800, 22850, 22900, 22950, 23000, 23050, 23100, 23150, 23200, 23250, 23300], "2026-05-08"
    )
    strikes, in_window = filter_atm_window_strikes(
        contracts, expiry="2026-05-08", atm=23000, step=50, window=2
    )
    assert strikes == [22900, 22950, 23000, 23050, 23100]
    # Each strike has CE+PE → 5 strikes x 2 sides = 10 rows
    assert len(in_window) == 10


def test_filter_window_drops_other_expiry() -> None:
    other = _expiry_set([23000], "2026-05-15")
    same = _expiry_set([23000], "2026-05-08")
    strikes, in_window = filter_atm_window_strikes(
        other + same, expiry="2026-05-08", atm=23000, step=50, window=0
    )
    assert strikes == [23000]
    assert len(in_window) == 2  # only the 2026-05-08 CE+PE


# ── build_option_chain_template ───────────────────────────────────────


def test_chain_template_skeleton() -> None:
    contracts = _expiry_set([22950, 23000, 23050], "2026-05-08")
    chain = build_option_chain_template(contracts, [22950, 23000, 23050])
    assert set(chain.keys()) == {"22950", "23000", "23050"}
    leaf = chain["23000"]["ce"]
    assert leaf["token"] == "CE_23000"
    assert leaf["ltp"] == 0 and leaf["oi"] == 0 and leaf["ts"] == 0
    # Both sides present
    assert chain["23000"]["pe"]["token"] == "PE_23000"


def test_chain_template_missing_side_is_none() -> None:
    contracts = [
        {
            "expiry": "2026-05-08",
            "instrument_type": "CE",
            "strike_price": 23000,
            "instrument_key": "CE_23000",
        }
    ]
    chain = build_option_chain_template(contracts, [23000])
    assert chain["23000"]["ce"]["token"] == "CE_23000"
    assert chain["23000"]["pe"] is None


# ── build_trading_basket ──────────────────────────────────────────────


def test_basket_picks_itm_strikes() -> None:
    contracts = _expiry_set([22900, 22950, 23000, 23050, 23100], "2026-05-08")
    chain = build_option_chain_template(contracts, [22900, 22950, 23000, 23050, 23100])
    basket = build_trading_basket(chain, atm=23000, step=50, range_n=2)
    # CE basket = ATM, ATM-1, ATM-2 strikes (ITM CE = lower strikes)
    assert basket["ce"] == ["CE_23000", "CE_22950", "CE_22900"]
    # PE basket = ATM, ATM+1, ATM+2 strikes (ITM PE = higher strikes)
    assert basket["pe"] == ["PE_23000", "PE_23050", "PE_23100"]


def test_basket_skips_missing_tokens() -> None:
    chain = {
        "23000": {"ce": {"token": "CE_23000"}, "pe": None},
        "23050": {"ce": None, "pe": {"token": "PE_23050"}},
    }
    basket = build_trading_basket(chain, atm=23000, step=50, range_n=1)
    assert basket["ce"] == ["CE_23000"]
    assert basket["pe"] == ["PE_23050"]
