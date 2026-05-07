# Modular Design

This document is the per-module breakdown for implementation. For each module, it specifies its sole responsibility, public interface (function signatures), dependencies, and testing surface.

A reader should be able to implement any single module by reading only its section here and the cross-referenced schema in `Schema.md`.

The strategy-engine module map lives in `Strategy.md` §7. This doc covers the rest of the engines.

---

## 1. Module Dependency Graph

```
brokers/upstox/*       (no internal deps; uses requests, websockets, protobuf)
       ↑
state/redis_client     state/postgres_client
       ↑
state/keys, state/streams, state/schemas/*    (constants + pydantic models)
       ↑
state/lua/*            (Lua scripts loaded by callers)
       ↑
log_setup.py
       ↑
engines/init/*
engines/data_pipeline/*
engines/strategy/*
engines/order_exec/*
engines/background/*
engines/scheduler/*
engines/health/*
engines/api_gateway/*
```

**Strict rules:**
- Engines never import each other.
- Engines never import individual broker modules. They go through the `UpstoxAPI` facade.
- Tests and one-off scripts MAY import individual broker modules directly.
- All engines import `state/*` and `log_setup`.

---

## 2. `brokers/upstox/` — SDK Catalog

The broker layer is a complete SDK with 21 modules + 1 facade. All REST calls return the standard envelope:

```python
{"success": bool, "data": Any, "error": str | None, "code": int | None, "raw": dict | None}
```

### 2.1 The facade — `client.py`

```python
from brokers.upstox import UpstoxAPI   # re-exported from __init__.py

res = UpstoxAPI.<method_name>(params: dict) -> dict | StreamerObject
```

- Stateless: `access_token` is passed inside `params` on every call.
- 45 classmethods covering auth, market metadata, options, orders, positions, streamers.
- Streamers return the live SDK object directly (raw, not envelope-wrapped).

### 2.2 Module catalog

| Group | Modules | Used by |
|---|---|---|
| **Auth & user** | `auth.py`, `profile.py`, `capital.py`, `kill_switch.py`, `static_ips.py` | Init, Background, FastAPI |
| **Market metadata** | `holidays.py`, `market_timings.py`, `market_status.py`, `market_data.py`, `instruments.py`, `historical_candles.py` | Init (precheck), Background, FastAPI |
| **Options** | `option_contract.py`, `option_chain.py`, `option_greeks.py`, `brokerage.py` | Init (basket build), Background (ΔPCR baseline), Order Exec (charges) |
| **Orders** | `orders.py` (v3 HFT place/modify/cancel + v2 reads + exit-all), `positions.py`, `rate_limiter.py` | Order Exec |
| **Streamers** | `market_streamer.py` (`MarketDataStreamerV3` wrapper), `portfolio_streamer.py` (`PortfolioDataStreamer` wrapper) | Data Pipeline, Background |
| **Facade** | `__init__.py`, `client.py` | All engines |

### 2.3 UpstoxAPI method index (selected)

| Method | Backing endpoint | Returns |
|---|---|---|
| `validate_token` | `/v2/user/profile` | `bool` |
| `request_access_token` | `/v3/login/auth/token/request/{client_id}` | envelope; data has `authorization_expiry`, `notifier_url` |
| `get_profile` / `get_capital` / `get_kill_switch_status` | various | envelope |
| `get_holidays` / `get_market_timings` / `get_market_status` | various | envelope |
| `is_holiday_for` / `is_standard_session` / `is_market_open` | (predicate) | `bool` (no I/O) |
| `get_ltp` | `/v3/market-quote/ltp` | envelope; data is `{key: ltp}` |
| `download_master_contract` | CDN | envelope; data has `gz_path`, `json_path`, `rows`, `segments` |
| `get_option_contracts` / `nearest_expiry` / `strikes_for` | `/v2/option/contract` + helpers | envelope / list |
| `get_option_chain` / `total_pcr` / `strikes_around_atm` | `/v2/option/chain` + helpers | envelope / list |
| `get_option_greeks` | `/v3/market-quote/option-greek` | envelope; data rekeyed by instrument_token |
| `get_brokerage` | `/v2/charges/brokerage` | envelope; data adds `net_value` |
| `place_order` / `modify_order` / `cancel_order` | `/v3/order/place` / `/modify` / `/cancel` | envelope |
| `get_order_status` / `get_order_history` / `get_order_book` / `get_trades_for_day` / `get_trades_by_order` | `/v2/order/...` | envelope |
| `exit_all_positions` | `/v2/order/positions/exit` | envelope |
| `get_positions` | `/v2/portfolio/short-term-positions` | envelope |
| `market_streamer` / `portfolio_streamer` | Upstox SDK WS | live streamer object (raw) |

Full method list with required/optional `params` keys is in the `UpstoxAPI` class docstring (`backend/brokers/upstox/client.py`). The package docstring (`backend/brokers/upstox/__init__.py`) carries the per-module catalog with one-line descriptions.

---

## 3. `state/redis_client.py`

**Responsibility**: Single source of Redis pools (async + sync).

```python
def init_pools(unix_socket_path: str = "/var/run/redis/redis.sock", max_connections: int = 32) -> None
def get_redis() -> redis.asyncio.Redis
def get_redis_sync() -> redis.Redis
async def close_pools() -> None
```

**Used by**: every engine.

---

## 4. `state/postgres_client.py`

```python
async def init_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> None
def get_pool() -> asyncpg.Pool
async def close_pool() -> None
```

**Used by**: Init, Order Exec, Background, FastAPI.

---

## 5. `state/keys.py`

Centralizes every Redis key namespace as a Python constant (one per key in `Schema.md` §1).

---

## 6. `state/streams.py`

Stream names, consumer-group helpers, pub/sub channel constants.

```python
async def ensure_consumer_group(redis, stream: str, group: str) -> None
async def xreadgroup_one(redis, stream: str, group: str, consumer: str, block_ms: int = 0) -> tuple[str, dict] | None
```

---

## 7. `state/schemas/`

Pydantic v2 models per Schema.md §5.

---

## 8. `state/lua/`

Atomic multi-key Redis operations.

| File | Purpose |
|---|---|
| `cleanup_position.lua` | DEL signal/order/position/status/broker keys; SREM open sets; SADD closed_today |
| `config_write_through.lua` | Atomic SET of `strategy:configs:{section}` after Postgres write |
| `capital_allocator_check_and_reserve.lua` | Atomic budget + concurrency check + counter increment |

---

## 9. `engines/init/`

### 9.1 `redis_template.py`

```python
TEMPLATE: dict[str, dict]
async def apply(redis, flush_runtime: bool = True) -> dict
async def flush_runtime_namespaces(redis) -> None
```

### 9.2 `postgres_hydrator.py`

```python
async def hydrate_all(redis, postgres) -> None
async def hydrate_user_account(redis, postgres) -> None
async def hydrate_credentials(redis, postgres) -> None
async def hydrate_configs(redis, postgres) -> None
async def hydrate_market_calendar(redis, postgres) -> None
```

### 9.3 `holiday_check.py`

```python
def is_trading_day_today(redis) -> bool
```

### 9.4 `auth_bootstrap.py`

```python
async def ensure_valid_token(redis) -> bool
async def run_playwright_login(creds: dict) -> str | None
async def wait_for_webhook_token(timeout_sec: int = 600) -> str | None
async def persist_token(redis, postgres, token: str) -> None
```

### 9.5 `instruments_loader.py`

```python
async def load_master_instruments(redis) -> int
```

### 9.6 `strike_basket_builder.py`

```python
async def build_for_index(redis, index: str) -> dict

# pure helpers
def compute_atm(spot: float, strike_step: int) -> int
def discover_nearest_expiry(contracts: list[dict], today: date) -> str
def filter_atm_window_strikes(contracts: list[dict], expiry: str, atm: int, step: int, window: int) -> list[dict]
def build_option_chain_template(contracts: list[dict], strikes: list[int]) -> dict
def build_trading_basket(option_chain: dict, atm: int, step: int, range_n: int) -> dict
```

### 9.7 `main.py`

```python
async def main() -> int
```

---

## 10. `engines/data_pipeline/`

### 10.1 `ws_io.py`
```python
async def ws_io_loop(state: DataPipelineState) -> None
```

### 10.2 `tick_processor.py`
```python
async def tick_processor_loop(state: DataPipelineState) -> None

# pure helpers
def parse_tick(raw_frame: dict) -> ParsedTick
def update_option_chain_leaf(chain: dict, strike: int, side: str, tick: ParsedTick) -> dict
def update_rolling_bar(existing_bar: dict, tick: ParsedTick) -> dict
```

### 10.3 `subscription_manager.py`
```python
async def subscription_manager_loop(state: DataPipelineState) -> None

# pure helpers
def compute_desired_set(spot_per_index: dict[str, float], cfgs: dict[str, dict]) -> set[str]
def diff_sets(current: set, desired: set) -> tuple[set, set]
```

### 10.4 `pre_market_subscriber.py`
```python
async def subscribe_at_premarket(state: DataPipelineState) -> None
```

### 10.5 `main.py`
```python
async def main() -> None
```

---

## 11. `engines/strategy/`

### 11.1 `premium_diff.py` (pure)
```python
def compute_diffs(current: dict[str, float], pre_open: dict[str, float]) -> dict[str, float]
def compute_sums(diffs: dict[str, float], ce_strikes: list[str], pe_strikes: list[str]) -> tuple[float, float]
def pick_highest_diff_strike(diffs: dict[str, float], strikes: list[str]) -> tuple[str | None, float]
def all_strikes_negative(diffs: dict[str, float], strikes: list[str]) -> bool
```

### 11.2 `decision.py` (pure)
```python
from typing import Literal

def decide_when_flat(
    sum_ce: float, sum_pe: float, delta: float,
    reversal_threshold: float, dominance_threshold: float,
) -> Literal["BUY_CE", "BUY_PE", "WAIT", "WAIT_RECOVERY"]:
    """Strategy.md §6.1 decision tree."""

def decide_when_in_ce(delta: float, threshold: float) -> Literal["FLIP_TO_PE", "HOLD"]: ...
def decide_when_in_pe(delta: float, threshold: float) -> Literal["FLIP_TO_CE", "HOLD"]: ...
def decide_when_cooldown(now_ts_ms: int, cooldown_until_ts_ms: int) -> Literal["CONTINUE_WAIT", "GO_FLAT"]: ...
```

State IDs: `FLAT` / `IN_CE` / `IN_PE` / `COOLDOWN` / `HALTED`. See Strategy.md §3.

### 11.3 `pre_open_snapshot.py`
```python
async def capture(redis, index: str, basket_tokens: list[str]) -> dict
```

### 11.4 `pipeline.py`
```python
def system_gates_pass(redis_snapshot: dict) -> tuple[bool, str]
def liquidity_gate_pass(order_book: dict, intended_lots: int, lot_size: int) -> tuple[bool, str]
def all_pre_signal_gates(redis, signal: Signal) -> tuple[bool, str]
```

### 11.5 `strategies/base.py`
```python
class StrategyInstance:
    index: str
    strike_step: int
    lot_size: int
    config: IndexConfig

    def __init__(self, redis_sync, config: IndexConfig): ...
    def run(self) -> None: ...
    # private lifecycle methods documented in TDD §5.5
```

### 11.6 `strategies/{nifty50,banknifty}.py`
```python
class NIFTY50Strategy(StrategyInstance):    index = "nifty50"
class BANKNIFTYStrategy(StrategyInstance):  index = "banknifty"
```

### 11.7 `main.py`
```python
def main() -> None
```

---

## 12. `engines/order_exec/`

### 12.1 `pre_entry_gate.py`
```python
def check(redis_sync, signal: Signal) -> tuple[bool, str]
```

### 12.2 `entry.py`
```python
@dataclass
class EntryResult:
    filled_qty: int
    avg_fill_price: float
    order_id: str
    order_events: list[dict]
    abandon_reason: str | None

def submit_and_monitor(redis_sync, signal: Signal, pos_id: str, broker_token: str) -> EntryResult
```

### 12.3 `exit_eval.py`
```python
def evaluate(redis_sync, position: Position, current_premium: float) -> tuple[bool, str]
```

### 12.4 `exit_submit.py`
```python
def submit_and_complete(redis_sync, position: Position, exit_reason: str, broker_token: str) -> ExitResult
```

### 12.5 `reporting.py`
```python
def build_report(position: Position, entry: EntryResult, exit: ExitResult, snapshots: list[MarketSnapshot]) -> ClosedPositionReport
async def persist_report(postgres, report: ClosedPositionReport) -> str
```

### 12.6 `cleanup.py`
```python
def cleanup(redis_sync, pos_id: str, sig_id: str, order_ids: list[str], index: str) -> None
```

### 12.7 `worker.py`
```python
def worker_loop(work_queue: queue.Queue, redis_sync, postgres) -> None
def process_signal(redis_sync, postgres, signal: Signal) -> None
```

### 12.8 `dispatcher.py`
```python
async def dispatcher_loop(redis_async, work_queue: queue.Queue) -> None
```

### 12.9 `main.py`
```python
def main() -> None
```

---

## 13. `engines/background/`

### 13.1 `position_ws.py`
```python
async def position_ws_loop(redis, broker_token: str) -> None
```

### 13.2 `pnl_computer.py`
```python
async def pnl_loop(redis, postgres, interval_sec: float = 1.0) -> None
def compute_pnl(positions: list[Position], current_premiums: dict[str, float]) -> PnLSnapshot
```

### 13.3 `delta_pcr/compute.py` (pure)
```python
def compute_delta_pcr(current_oi: dict, previous_oi: dict) -> tuple[float, int, int]
```

### 13.4 `delta_pcr/{nifty50,banknifty}_thread.py`
```python
def run(redis_sync, postgres) -> None
```

### 13.5 `token_refresh.py`
```python
async def token_refresh_loop(redis, interval_sec: int = 300) -> None
```

### 13.6 `capital_poll.py`
```python
async def capital_poll_loop(redis, interval_sec: int = 30) -> None
```

### 13.7 `kill_switch_poll.py`
```python
async def kill_switch_poll_loop(redis, interval_sec: int = 30) -> None
```

### 13.8 `main.py`
```python
def main() -> None
```

---

## 14. `engines/scheduler/`

### 14.1 `tasks.py`
```python
@dataclass
class TaskDefinition:
    name: str
    cron: str
    event: str
    targets: list[str]

TASKS: list[TaskDefinition] = [ ... ]   # see TDD §8.1
```

### 14.2 `main.py`
```python
async def main() -> None
```

---

## 15. `engines/health/`

### 15.1 `heartbeat_watcher.py`
```python
async def heartbeat_loop(redis, interval_sec: float = 1.0) -> None
```

### 15.2 `dependency_probes.py`
```python
async def probe_redis(redis) -> DependencyStatus
async def probe_postgres(pool) -> DependencyStatus
async def probe_broker_auth(redis) -> DependencyStatus
async def probe_broker_kill_switch(redis) -> DependencyStatus
async def probe_loop(redis, pool, interval_sec: int = 10) -> None
```

### 15.3 `main.py`
```python
async def main() -> None
```

---

## 16. `engines/api_gateway/`

See `API.md` for full contract. Module structure documented there.

---

## 17. `log_setup.py`

```python
def configure(engine_name: str, level: str = "INFO") -> None
```

Single-call interface used by every engine's `main.py`.

---

## 18. View Builders (per-engine)

Each engine that owns view keys runs an async builder coroutine:

```python
async def view_builder_loop(redis, view_name: str, build_fn: Callable[[], dict], debounce_ms: int = 100) -> None
```

`build_fn` reads underlying state from Redis and produces the view JSON.

Owners (per HLD §7):
- Order Exec: `ui:views:position:{index}` × 2, `ui:views:positions_closed_today`
- Strategy: `ui:views:strategy:{index}` × 2
- Background: `ui:views:dashboard`, `ui:views:pnl`, `ui:views:capital`, `ui:views:delta_pcr:{index}` × 2
- Health: `ui:views:health`
- FastAPI: `ui:views:configs` (on edit)

---

## 19. Implementation Order

1. `state/redis_client.py`, `state/postgres_client.py`, `state/keys.py`, `state/streams.py`
2. All `state/schemas/*.py` pydantic models
3. `db/migrations/` Postgres DDL — run against local Postgres
4. `log_setup.py`
5. `brokers/upstox/option_contract.py`, `option_chain.py`, `instrument_search.py` (new modules)
6. `engines/init/redis_template.py` (full canonical schema)
7. `engines/init/holiday_check.py`, `auth_bootstrap.py`, `instruments_loader.py`, `strike_basket_builder.py`, `postgres_hydrator.py`
8. `engines/init/main.py`
9. `engines/health/`
10. `engines/data_pipeline/` (pure helpers first, then loops, then main)
11. `engines/strategy/` (pure functions first, then base.py, then per-index modules, then main)
12. `engines/background/` (position_ws + delta_pcr threads + pnl + utility threads)
13. `engines/order_exec/` (paper-mode lifecycle first; then live with full entry/exit/modify/cancel)
14. `engines/scheduler/`
15. `engines/api_gateway/` (per API.md)
16. `deploy/systemd/` service units

---

## 20. Cross-Engine Communication Contracts

See HLD §6 for the full table. Adding a new contract requires updating both `HLD.md` §6 and `Schema.md` §1.

---

## 21. Testing Surface per Module

| Module | Test type | What to assert |
|---|---|---|
| `state/redis_client.py` | integration | pool init, sync/async coexistence |
| `state/postgres_client.py` | integration | pool init, basic query |
| `state/schemas/*` | unit | pydantic validation passes/fails on representative payloads |
| `brokers/upstox/option_contract.py` | unit + integration | URL + headers correct; live `test()` returns >0 contracts for NIFTY |
| `engines/init/holiday_check.py` | unit | true/false based on Redis SET membership |
| `engines/init/auth_bootstrap.py` | integration | mock invalid token → triggers refresh path |
| `engines/init/strike_basket_builder.py` | unit (pure helpers) + integration (full build_for_index) | atm correct, expiry discovered, chain template shape correct |
| `engines/data_pipeline/tick_processor.py` | unit (pure helpers) | parse_tick correct; option_chain leaf updated correctly |
| `engines/strategy/premium_diff.py` | unit | exhaustive: zero, negative, ties |
| `engines/strategy/decision.py` | unit | every state-machine path |
| `engines/order_exec/pre_entry_gate.py` | unit | every reject reason fires correctly |
| `engines/order_exec/exit_eval.py` | unit | priority order verified |
| `engines/background/delta_pcr/compute.py` | unit | new-strike, exited-strike, normal cases |
| `engines/background/pnl_computer.py` | unit | compute_pnl for representative positions |
| `engines/api_gateway/*` | integration | each endpoint with auth and validation |

End-to-end: full system in paper mode against recorded broker WS replay; verify expected closed_positions match.
