"""
brokers.upstox.client — `UpstoxAPI` facade: the single import-and-use entry
point for every broker call.

Design contract:
  - All methods are @classmethod / @staticmethod on `UpstoxAPI`. No instance.
  - The class is fully STATELESS. Pass `access_token` (and any other params)
    inside `params` on every REST call.
  - Every REST classmethod takes a single `params: dict[str, Any]` and forwards
    `**params` to the underlying module function. Dict keys must match the
    function argument names — typos raise TypeError (desired strictness).
  - REST methods return the standard envelope:
      {success, data, error, code, raw}.
  - Streamer methods return the LIVE SDK streamer object (not envelope-wrapped).
  - Predicate / pure-helper methods are @staticmethods returning native types.

The facade is THIN — zero business logic. Every classmethod is a one-line
forwarder; the underlying modules carry URLs, HTTP, validation, normalization.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox import auth as _auth
from brokers.upstox import brokerage as _brokerage
from brokers.upstox import capital as _capital
from brokers.upstox import historical_candles as _historical_candles
from brokers.upstox import holidays as _holidays
from brokers.upstox import instruments as _instruments
from brokers.upstox import kill_switch as _kill_switch
from brokers.upstox import market_data as _market_data
from brokers.upstox import market_status as _market_status
from brokers.upstox import market_streamer as _market_streamer
from brokers.upstox import market_timings as _market_timings
from brokers.upstox import option_chain as _option_chain
from brokers.upstox import option_contract as _option_contract
from brokers.upstox import option_greeks as _option_greeks
from brokers.upstox import orders as _orders
from brokers.upstox import portfolio_streamer as _portfolio_streamer
from brokers.upstox import positions as _positions
from brokers.upstox import profile as _profile
from brokers.upstox import static_ips as _static_ips


class UpstoxAPI:
    """Single-import facade for the entire Upstox broker layer."""

    # ── Auth & user ────────────────────────────────────────────────────

    @classmethod
    def validate_token(cls, params: dict[str, Any]) -> bool:
        return _auth.is_token_valid_remote(
            token=params["access_token"], timeout=params.get("timeout", 5)
        )

    @classmethod
    def request_access_token(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _auth.request_access_token(**params)

    @classmethod
    def get_profile(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _profile.get_profile(**params)

    @classmethod
    def get_capital(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _capital.get_capital(**params)

    @classmethod
    def get_kill_switch_status(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _kill_switch.get_kill_switch_status(**params)

    @classmethod
    def set_kill_switch(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _kill_switch.set_kill_switch(**params)

    @staticmethod
    def is_segment_blocked(snapshot: list[dict[str, Any]] | None, segment: str = "NSE_FO") -> bool:
        return _kill_switch.is_segment_blocked(snapshot, segment)

    @classmethod
    def get_static_ips(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _static_ips.get_static_ips(**params)

    @classmethod
    def update_static_ips(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _static_ips.update_static_ips(**params)

    # ── Market metadata ────────────────────────────────────────────────

    @classmethod
    def get_holidays(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _holidays.get_holidays(**params)

    @classmethod
    def get_holiday_by_date(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _holidays.get_holiday_by_date(**params)

    @staticmethod
    def is_holiday_for(entries: list[dict[str, Any]] | None, exchange: str = "NSE") -> bool:
        return _holidays.is_holiday_for(entries, exchange)

    @classmethod
    def get_market_timings(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _market_timings.get_market_timings(**params)

    @staticmethod
    def is_standard_session(entries: list[dict[str, Any]] | None, exchange: str = "NSE") -> bool:
        return _market_timings.is_standard_session(entries, exchange)

    @classmethod
    def get_market_status(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _market_status.get_market_status(**params)

    @staticmethod
    def is_market_open(status: str | None) -> bool:
        return _market_status.is_open(status)

    @staticmethod
    def is_market_pre_open(status: str | None) -> bool:
        return _market_status.is_pre_open(status)

    @classmethod
    def get_ltp(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _market_data.get_ltp(**params)

    @classmethod
    def download_master_contract(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _instruments.download_master_contract(**params)

    @classmethod
    def get_historical_candles(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _historical_candles.get_historical_candles(**params)

    @classmethod
    def get_intraday_candles(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _historical_candles.get_intraday_candles(**params)

    # ── Options ────────────────────────────────────────────────────────

    @classmethod
    def get_option_contracts(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _option_contract.get_option_contracts(**params)

    @staticmethod
    def expiries_for(contracts: list[dict[str, Any]] | None) -> list[str]:
        return _option_contract.expiries_for(contracts)

    @staticmethod
    def nearest_expiry(contracts: list[dict[str, Any]] | None, today: Any = None) -> str | None:
        return _option_contract.nearest_expiry(contracts, today=today)

    @staticmethod
    def strikes_for(
        contracts: list[dict[str, Any]] | None, expiry: str, instrument_type: str = "CE"
    ) -> list[dict[str, Any]]:
        return _option_contract.strikes_for(contracts, expiry, instrument_type)

    @classmethod
    def get_option_chain(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _option_chain.get_option_chain(**params)

    @staticmethod
    def total_pcr(chain: list[dict[str, Any]] | None) -> float | None:
        return _option_chain.total_pcr(chain)

    @staticmethod
    def strikes_around_atm(
        chain: list[dict[str, Any]] | None,
        spot: float | None = None,
        n_each_side: int = 6,
        strike_step: float | None = None,
    ) -> list[dict[str, Any]]:
        return _option_chain.strikes_around_atm(chain, spot, n_each_side, strike_step)

    @classmethod
    def get_option_greeks(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _option_greeks.get_option_greeks(**params)

    @classmethod
    def get_brokerage(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _brokerage.get_brokerage(**params)

    # ── Orders ─────────────────────────────────────────────────────────

    @classmethod
    def place_order(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.place_order(**params)

    @classmethod
    def modify_order(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.modify_order(**params)

    @classmethod
    def cancel_order(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.cancel_order(**params)

    @classmethod
    def get_order_status(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.get_order_status(**params)

    @classmethod
    def get_order_history(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.get_order_history(**params)

    @classmethod
    def get_order_book(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.get_order_book(**params)

    @classmethod
    def get_trades_for_day(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.get_trades_for_day(**params)

    @classmethod
    def get_trades_by_order(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.get_trades_by_order(**params)

    @classmethod
    def exit_all_positions(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _orders.exit_all_positions(**params)

    @staticmethod
    def save_api_log(
        api_logs_path: str, log_type: str, response: dict[str, Any], identifier: str
    ) -> None:
        return _orders.save_api_log(api_logs_path, log_type, response, identifier)

    # ── Positions ──────────────────────────────────────────────────────

    @classmethod
    def get_positions(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _positions.get_positions(**params)

    # ── Streamers (raw SDK objects) ────────────────────────────────────

    @classmethod
    def market_streamer(cls, params: dict[str, Any]) -> Any:
        return _market_streamer.start_streamer(**params)

    @classmethod
    def build_market_streamer(cls, params: dict[str, Any]) -> Any:
        return _market_streamer.build_streamer(**params)

    @classmethod
    def portfolio_streamer(cls, params: dict[str, Any]) -> Any:
        return _portfolio_streamer.start_streamer(**params)

    @classmethod
    def build_portfolio_streamer(cls, params: dict[str, Any]) -> Any:
        return _portfolio_streamer.build_streamer(**params)
