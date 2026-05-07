# Technical Design Document — Multi-Strategy Trading Bot

This document specifies the modular design and per-engine implementation contracts. An engineer reading this should be able to implement each engine independently with no further architectural decisions.

> **Note (2026-05-07):** the strategy-engine contract in this doc reflects
> the deprecated premium-diff design. The current strategy engine is
> documented authoritatively in `Strategy.md`. The other engine contracts
> (data pipeline, order exec, background, scheduler, health, init, FastAPI)
> are still current.

---

## 1. Module Layout (`backend/`)

```
backend/
├── brokers/upstox/                  # broker SDK; engines import via UpstoxAPI facade
│   ├── __init__.py                  # re-exports UpstoxAPI
│   ├── client.py                    # UpstoxAPI: 45 stateless classmethods
│   ├── auth.py                      # OAuth2 v2 + v3 token request + token-validity probe
│   ├── profile.py                   # /v2/user/profile
│   ├── capital.py                   # /v3/user/get-funds-and-margin
│   ├── kill_switch.py               # /v2/user/kill-switch
│   ├── static_ips.py                # /v2/user/ip
│   ├── holidays.py                  # /v2/market/holidays + is_holiday_for predicate
│   ├── market_timings.py            # /v2/market/timings/{date}
│   ├── market_status.py             # /v2/market/status/{exchange}
│   ├── market_data.py               # /v3/market-quote/ltp
│   ├── instruments.py               # CDN NSE.json.gz master contract
│   ├── historical_candles.py        # /v3/historical-candle/...
│   ├── option_contract.py           # /v2/option/contract
│   ├── option_chain.py              # /v2/option/chain
│   ├── option_greeks.py             # /v3/market-quote/option-greek
│   ├── brokerage.py                 # /v2/charges/brokerage
│   ├── orders.py                    # v3 HFT place/modify/cancel + v2 reads + exit-all
│   ├── positions.py                 # /v2/portfolio/short-term-positions
│   ├── rate_limiter.py              # per-second rate-limit helpers
│   ├── market_streamer.py           # MarketDataStreamerV3 wrapper (raw SDK)
│   └── portfolio_streamer.py        # PortfolioDataStreamer wrapper (raw SDK)
├── state/
│   ├── redis_client.py              # async pool, Unix socket, orjson
│   ├── postgres_client.py           # asyncpg pool
│   ├── keys.py                      # all Redis key constants under 6 namespaces
│   ├── streams.py                   # stream names + consumer-group helpers
│   ├── lua/                         # atomic multi-key Lua scripts
│   │   ├── cleanup_position.lua
│   │   └── config_write_through.lua
│   └── schemas/                     # pydantic v2 models per Schema.md §5
├── engines/
│   ├── init_engine/
│   │   ├── main.py
│   │   ├── redis_template.py        # canonical schema as dict-of-dicts
│   │   ├── postgres_hydrator.py
│   │   ├── holiday_check.py         # NEW: market_calendar lookup; abort if non-trading
│   │   ├── auth_bootstrap.py        # NEW: validate / refresh access token before any engine starts
│   │   ├── instruments_loader.py    # bulk instrument cache
│   │   └── strike_basket_builder.py # NEW: per-index ATM compute + basket build + subscription:desired
│   ├── data_pipeline_engine/
│   │   ├── main.py
│   │   ├── ws_io.py
│   │   ├── tick_processor.py
│   │   ├── subscription_manager.py
│   │   └── pre_market_subscriber.py # NEW: at 09:14:00, subscribe broker WS to all 54 instruments
│   ├── strategy_engine/
│   │   ├── main.py
│   │   ├── strategies/
│   │   │   ├── base.py
│   │   │   ├── nifty50.py
│   │   │   └── banknifty.py
│   │   ├── premium_diff.py
│   │   ├── decision.py
│   │   ├── pre_open_snapshot.py
│   │   └── pipeline.py              # shared pre-signal pipeline
│   ├── order_execution_engine/
│   │   ├── main.py
│   │   ├── dispatcher.py
│   │   ├── worker.py
│   │   ├── entry.py                 # DAY limit + monitor + modify/cancel
│   │   ├── exit_eval.py
│   │   ├── exit_submit.py           # DAY limit + modify-only loop
│   │   ├── pre_entry_gate.py
│   │   ├── reporting.py
│   │   └── cleanup.py
│   ├── background_engine/
│   │   ├── main.py
│   │   ├── position_ws.py
│   │   ├── pnl_computer.py
│   │   ├── delta_pcr/
│   │   │   ├── compute.py
│   │   │   ├── nifty50_thread.py
│   │   │   └── banknifty_thread.py
│   │   ├── token_refresh.py
│   │   ├── capital_poll.py
│   │   └── kill_switch_poll.py
│   ├── scheduler_engine/
│   │   ├── main.py
│   │   └── tasks.py
│   ├── health_engine/
│   │   ├── main.py
│   │   ├── heartbeat_watcher.py
│   │   └── dependency_probes.py
│   └── api_gateway/
│       ├── main.py
│       ├── ws_endpoints.py
│       ├── rest/
│       └── webhooks/
├── deploy/systemd/                  # one .service file per engine
├── db/migrations/                   # Postgres DDL per Schema.md §2
└── log_setup.py                     # central loguru config
```

---

## 1.1 Broker Access Pattern

Engines **never** import individual broker modules. They go through the `UpstoxAPI` facade:

```python
from brokers.upstox import UpstoxAPI

res = UpstoxAPI.get_capital({"access_token": tok})
if res["success"]:
    funds = res["data"]
```

- All REST methods return the standard envelope `{success, data, error, code, raw}`.
- All methods take a single `params: dict`; required keys raise `TypeError` if missing (strict by design).
- Streamer methods (`market_streamer`, `portfolio_streamer`) return the **raw** SDK streamer object (NOT envelope-wrapped); the caller wires events.
- Predicates (`is_holiday_for`, `is_market_open`, etc.) never hit the network.

Full method index is in `backend/brokers/upstox/client.py` and `backend/brokers/upstox/__init__.py`. See `Modular_Design.md` §2 for the per-method signature table.

Tests and one-off scripts MAY import individual broker modules directly. Engine code MAY NOT.

---

## 2. Shared Primitives (`backend/state/`)

### 2.1 `redis_client.py`

```python
import redis.asyncio as redis
import redis as redis_sync_lib
from typing import Optional

_pool_async: Optional[redis.ConnectionPool] = None
_pool_sync: Optional[redis_sync_lib.ConnectionPool] = None

def init_pools(unix_socket_path: str = "/var/run/redis/redis.sock", max_connections: int = 32) -> None:
    global _pool_async, _pool_sync
    _pool_async = redis.ConnectionPool.from_url(f"unix://{unix_socket_path}", max_connections=max_connections, decode_responses=False)
    _pool_sync = redis_sync_lib.ConnectionPool.from_url(f"unix://{unix_socket_path}", max_connections=max_connections, decode_responses=False)

def get_redis() -> redis.Redis: ...                      # async, for asyncio engines
def get_redis_sync() -> redis_sync_lib.Redis: ...        # sync, for Order Exec worker threads
async def close_pools() -> None: ...
```

### 2.2 `postgres_client.py`

```python
import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

async def init_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> None: ...
def get_pool() -> asyncpg.Pool: ...
async def close_pool() -> None: ...
```

### 2.3 `keys.py`

Every key from Schema.md §1 has a constant here. Examples:
```python
# system
SYSTEM_FLAGS_READY                   = "system:flags:ready"
SYSTEM_FLAGS_TRADING_ACTIVE          = "system:flags:trading_active"
SYSTEM_HEALTH_HEARTBEATS             = "system:health:heartbeats"      # HASH, fields = engine names
SYSTEM_SCHEDULER_TRADING_DAYS        = "system:scheduler:market_calendar:trading_days"

# user
USER_AUTH_ACCESS_TOKEN               = "user:auth:access_token"
USER_CAPITAL_FUNDS                   = "user:capital:funds"

# market_data
MARKET_DATA_INSTRUMENTS_MASTER       = "market_data:instruments:master"
MARKET_DATA_INDEX_META               = "market_data:indexes:{index}:meta"
MARKET_DATA_INDEX_SPOT               = "market_data:indexes:{index}:spot"
MARKET_DATA_INDEX_OPTION_CHAIN       = "market_data:indexes:{index}:option_chain"
MARKET_DATA_SUBSCRIPTIONS_SET        = "market_data:subscriptions:set"
MARKET_DATA_SUBSCRIPTIONS_DESIRED    = "market_data:subscriptions:desired"

# strategy
STRATEGY_CONFIG_INDEX                = "strategy:configs:indexes:{index}"
STRATEGY_STATE                       = "strategy:{index}:state"
STRATEGY_BASKET                      = "strategy:{index}:basket"
STRATEGY_PRE_OPEN                    = "strategy:{index}:pre_open"
STRATEGY_LIVE_SUM_CE                 = "strategy:{index}:live:sum_ce"
STRATEGY_LIVE_SUM_PE                 = "strategy:{index}:live:sum_pe"
STRATEGY_DELTA_PCR_CUMULATIVE        = "strategy:{index}:delta_pcr:cumulative"
STRATEGY_SIGNAL                      = "strategy:signals:{sig_id}"

# orders
ORDERS_POSITIONS                     = "orders:positions:{pos_id}"
ORDERS_POSITIONS_OPEN                = "orders:positions:open"
ORDERS_POSITIONS_OPEN_BY_INDEX       = "orders:positions:open_by_index:{index}"
ORDERS_STATUS                        = "orders:status:{pos_id}"
ORDERS_BROKER_POS                    = "orders:broker:pos:{order_id}"
ORDERS_PNL_REALIZED                  = "orders:pnl:realized"
ORDERS_PNL_PER_INDEX                 = "orders:pnl:per_index:{index}"

# ui
UI_VIEW                              = "ui:views:{name}"
UI_DIRTY                             = "ui:dirty"
```

### 2.4 `streams.py`

```python
SYSTEM_STREAM_CONTROL                = "system:stream:control"
MARKET_DATA_STREAM_TICK              = "market_data:stream:tick:{index}"
STRATEGY_STREAM_SIGNALS              = "strategy:stream:signals"
STRATEGY_STREAM_REJECTED             = "strategy:stream:rejected_signals"
ORDERS_STREAM_ORDER_EVENTS           = "orders:stream:order_events"
ORDERS_STREAM_MANUAL_EXIT            = "orders:stream:manual_exit"
UI_STREAM_HEALTH_ALERTS              = "ui:stream:health_alerts"

CHANNEL_VIEW                         = "ui:pub:view"
CHANNEL_SYSTEM_EVENT                 = "system:pub:system_event"

GROUP_ORDER_EXEC                     = "exec"

async def ensure_consumer_group(redis, stream: str, group: str) -> None: ...
async def xreadgroup_one(redis, stream: str, group: str, consumer: str, block_ms: int = 0) -> tuple[str, dict] | None: ...
```

### 2.5 `lua/`

| File | Purpose |
|---|---|
| `cleanup_position.lua` | DEL all `strategy:signals:{sig_id}`, `orders:orders:{*}`, `orders:positions:{pos_id}`, `orders:status:{pos_id}`, `orders:broker:pos:{*}` keys; SREM from `orders:positions:open`, `orders:positions:open_by_index:{index}`; SADD to `orders:positions:closed_today`. |
| `config_write_through.lua` | Atomic SET of `strategy:configs:{section}` after Postgres write succeeds. |
| `capital_allocator_check_and_reserve.lua` | Atomic check of strategy budget + global cap + index slot, increment counters if pass. |

---

## 3. Init Engine

### 3.1 `main.py`

```python
async def main() -> int:
    await redis_client.init_pools(...)
    await postgres_client.init_pool(...)

    await redis_template.apply(redis, flush_runtime=True)
    await postgres_hydrator.hydrate_all(redis, postgres)

    if not holiday_check.is_trading_day_today(redis):
        await redis.set("system:flags:trading_active", "false")
        log.info("Non-trading day; engines will idle.")
        return 0

    if not await auth_bootstrap.ensure_valid_token(redis):
        await redis.set("system:flags:init_failed", "auth_bootstrap_failed")
        return 1

    for index in get_enabled_indexes(redis):
        await strike_basket_builder.build_for_index(redis, index)

    await redis.set("system:flags:ready", "true")
    await redis.publish("system:pub:system_event", '{"event":"ready"}')
    return 0
```

### 3.2 `redis_template.py`
Hardcoded canonical schema. `apply()` walks the dict and writes defaults.

```python
TEMPLATE: dict[str, dict] = { ... }   # one entry per key in Schema.md §1

async def apply(redis, flush_runtime: bool = True) -> dict: ...
async def flush_runtime_namespaces(redis) -> None: ...
```

### 3.3 `postgres_hydrator.py`
```python
async def hydrate_all(redis, postgres) -> None
async def hydrate_user_account(redis, postgres) -> None
async def hydrate_credentials(redis, postgres) -> None     # decrypts in memory; never logs
async def hydrate_configs(redis, postgres) -> None
async def hydrate_market_calendar(redis, postgres) -> None
```

### 3.4 `holiday_check.py`
```python
def is_trading_day_today(redis) -> bool:
    today = date.today().isoformat()
    return redis.sismember("system:scheduler:market_calendar:trading_days", today)
```

### 3.5 `auth_bootstrap.py`
```python
async def ensure_valid_token(redis) -> bool:
    """Check current token; if invalid, run Playwright login or block until v3 webhook delivers."""
    creds = read_credentials(redis)
    cached = read_cached_token(redis)
    if cached and auth.is_token_valid_remote(cached["token"]):
        return True
    new_token = run_playwright_login(creds) or wait_for_webhook_token(timeout=600)
    if new_token:
        persist_token(redis, postgres, new_token)
        return True
    return False
```

### 3.6 `instruments_loader.py`
```python
async def load_master_instruments(redis) -> int:
    """Bulk load all NSE+BSE instruments into market_data:instruments:master HASH."""
```

### 3.7 `strike_basket_builder.py`
```python
async def build_for_index(redis, index: str) -> dict:
    """For one index: fetch spot, compute ATM, fetch contracts, discover expiry, build basket + chain template + subscription:desired."""

    cfg = read_index_config(redis, index)
    spot_token = INDEX_SPOT_TOKENS[index]

    # 1. Fetch spot
    quote = market_data.get_full_quote(headers, [spot_token])
    spot_ltp = parse_ltp(quote)
    prev_close = parse_close(quote)

    # 2. Compute ATM
    atm = round(spot_ltp / cfg["strike_step"]) * cfg["strike_step"]

    # 3. Fetch all option contracts (no expiry filter)
    all_contracts = option_contract.fetch(headers, instrument_key=spot_token, expiry=None)

    # 4. Discover nearest expiry
    today = date.today()
    nearest_expiry = min(c["expiry"] for c in all_contracts if c["expiry"] >= today.isoformat())

    # 5. Filter to ATM ± 6 strikes for that expiry
    window = cfg["subscription_range"]
    strikes_in_window = list(range(atm - window * cfg["strike_step"], atm + (window + 1) * cfg["strike_step"], cfg["strike_step"]))
    chain_contracts = [c for c in all_contracts if c["expiry"] == nearest_expiry and c["strike_price"] in strikes_in_window]

    # 6. Build option_chain template with empty WS placeholders
    option_chain = {}
    for strike in strikes_in_window:
        ce = next((c for c in chain_contracts if c["strike_price"] == strike and c["instrument_type"] == "CE"), None)
        pe = next((c for c in chain_contracts if c["strike_price"] == strike and c["instrument_type"] == "PE"), None)
        option_chain[str(strike)] = {
            "ce": {"token": ce["instrument_key"], "ltp": 0, "bid": 0, "ask": 0, "bid_qty": 0, "ask_qty": 0, "vol": 0, "oi": 0, "ts": 0} if ce else None,
            "pe": {"token": pe["instrument_key"], "ltp": 0, "bid": 0, "ask": 0, "bid_qty": 0, "ask_qty": 0, "vol": 0, "oi": 0, "ts": 0} if pe else None,
        }

    # 7. Trading basket (locked at ATM ± trading_basket_range)
    basket = {
        "ce": [option_chain[str(atm - i * cfg["strike_step"])]["ce"]["token"] for i in range(cfg["trading_basket_range"] + 1)],
        "pe": [option_chain[str(atm + i * cfg["strike_step"])]["pe"]["token"] for i in range(cfg["trading_basket_range"] + 1)],
    }

    # 8. Persist to Redis
    await redis.set(f"market_data:indexes:{index}:meta", orjson.dumps({
        "strike_step": cfg["strike_step"], "lot_size": chain_contracts[0]["lot_size"],
        "exchange": cfg["exchange"], "spot_token": spot_token, "expiry": nearest_expiry,
        "prev_close": prev_close, "atm_at_open": atm,
        "ce_strikes": [s for s in strikes_in_window], "pe_strikes": [s for s in strikes_in_window],
    }))
    await redis.set(f"market_data:indexes:{index}:option_chain", orjson.dumps(option_chain))
    await redis.set(f"strategy:{index}:basket", orjson.dumps(basket))

    # 9. Add tokens to subscription:desired
    all_tokens = {c["instrument_key"] for c in chain_contracts}
    all_tokens.add(spot_token)
    await redis.sadd("market_data:subscriptions:desired", *all_tokens)

    return {"atm": atm, "expiry": nearest_expiry, "tokens": len(all_tokens)}
```

`INDEX_SPOT_TOKENS = {"nifty50": "NSE_INDEX|Nifty 50", "banknifty": "NSE_INDEX|Nifty Bank"}`

---

## 4. Data Pipeline Engine

### 4.1 `ws_io.py`
```python
async def ws_io_loop(state: DataPipelineState) -> None:
    """Owns broker market WS. Decodes protobuf, pushes ticks to async queue. Reconnects with backoff."""
```

### 4.2 `tick_processor.py`
```python
async def tick_processor_loop(state: DataPipelineState) -> None:
    """Drains tick_queue. For each tick:
    - Look up (index, strike, ce|pe) from instruments:master
    - Update market_data:indexes:{index}:option_chain leaf fields atomically
    - Update market_data:indexes:{index}:spot if it's a spot tick
    - Update bars:1s:{token} rolling OHLC
    - XADD market_data:stream:tick:{index} *
    """

# pure helpers
def parse_tick(raw_frame: dict) -> ParsedTick: ...
def update_option_chain_leaf(chain: dict, strike: int, side: str, tick: ParsedTick) -> dict: ...
def update_rolling_bar(existing_bar: dict, tick: ParsedTick) -> dict: ...
```

### 4.3 `subscription_manager.py`
```python
async def subscription_manager_loop(state: DataPipelineState) -> None:
    """Watches each index's spot. When ATM shifts, computes new ATM±6 set, diffs, sends sub/unsub."""

# pure helpers
def compute_desired_set(spot_per_index: dict[str, float], cfgs: dict[str, dict]) -> set[str]: ...
def diff_sets(current: set, desired: set) -> tuple[set, set]: ...
```

### 4.4 `pre_market_subscriber.py`
```python
async def subscribe_at_premarket(state: DataPipelineState) -> None:
    """At 09:14:00 (or as soon as possible after Init completes), subscribe broker WS to all tokens in market_data:subscriptions:desired."""
```

### 4.5 `main.py`
```python
async def main() -> None:
    state = DataPipelineState(...)
    await asyncio.gather(
        ws_io_loop(state),
        tick_processor_loop(state),
        subscription_manager_loop(state),
        pre_market_subscriber.subscribe_at_premarket(state),
        view_builder_loop(...),
    )
```

---

## 5. Strategy Engine

### 5.1 `premium_diff.py` (pure)
```python
def compute_diffs(current: dict[str, float], pre_open: dict[str, float]) -> dict[str, float]
def compute_sums(diffs: dict[str, float], ce_strikes: list[str], pe_strikes: list[str]) -> tuple[float, float]
def pick_highest_diff_strike(diffs: dict[str, float], strikes: list[str]) -> tuple[str | None, float]
def all_strikes_negative(diffs: dict[str, float], strikes: list[str]) -> bool
```

### 5.2 `decision.py` (pure)
```python
from typing import Literal

def decide_when_flat(
    sum_ce: float, sum_pe: float, delta: float,
    reversal_threshold: float, dominance_threshold: float,
) -> Literal["BUY_CE", "BUY_PE", "WAIT", "WAIT_RECOVERY"]:
    """Strategy.md §6.1 decision tree. WAIT_RECOVERY when both SUMs<=0;
    BUY_* when one side dominates; WAIT otherwise."""

def decide_when_in_ce(delta: float, threshold: float) -> Literal["FLIP_TO_PE", "HOLD"]:
    """FLIP_TO_PE when delta > +threshold."""

def decide_when_in_pe(delta: float, threshold: float) -> Literal["FLIP_TO_CE", "HOLD"]:
    """FLIP_TO_CE when delta < -threshold."""

def decide_when_cooldown(now_ts_ms: int, cooldown_until_ts_ms: int) -> Literal["CONTINUE_WAIT", "GO_FLAT"]:
    """Pure timer check; transitions COOLDOWN -> FLAT."""
```

State IDs (one of): `FLAT`, `IN_CE`, `IN_PE`, `COOLDOWN`, `HALTED`. See Strategy.md §3.

### 5.3 `pre_open_snapshot.py`
```python
async def capture(redis, index: str, basket_tokens: list[str]) -> dict
```

### 5.4 `pipeline.py` (shared pre-signal)
```python
def system_gates_pass(redis_snapshot: dict) -> tuple[bool, str]
def liquidity_gate_pass(order_book: dict, intended_lots: int, lot_size: int) -> tuple[bool, str]
def all_pre_signal_gates(redis, signal: Signal) -> tuple[bool, str]
```

### 5.5 `strategies/base.py`
```python
class StrategyInstance:
    index: str
    strike_step: int
    lot_size: int
    config: IndexConfig

    def __init__(self, redis_sync, config: IndexConfig): ...

    def run(self) -> None:
        self._wait_for_system_ready()
        self._wait_for_pre_open_snapshot_time()
        self._capture_pre_open_snapshot()         # fail-closed if any zero-ts strike
        self._wait_for_settle_window_end()         # 09:15:00 -> 09:15:09
        self._enter_continuous_loop()              # 09:15:10 -> 15:15

    def _wait_for_system_ready(self) -> None: ...
    def _wait_for_pre_open_snapshot_time(self) -> None: ...
    def _capture_pre_open_snapshot(self) -> None: ...
    def _wait_for_settle_window_end(self) -> None: ...
    def _enter_continuous_loop(self) -> None: ...
    def _on_tick(self, tick_event: dict) -> None: ...      # main per-tick path
    def _evaluate_state_machine(self, sum_ce, sum_pe, delta) -> Optional[Signal]: ...
    def _emit_signal(self, intent: SignalIntent, side: str, strike: int, ...) -> str: ...
    def _enter_cooldown(self, reason: Literal["POST_SL", "POST_REVERSAL"]) -> None: ...
    def _maybe_exit_cooldown(self) -> None: ...
    def _is_in_entry_freeze(self) -> bool: ...             # 15:10 -> 15:15
    def _at_daily_caps(self) -> bool: ...                   # entries / reversals
    def _heartbeat(self) -> None: ...
    def _is_market_open(self) -> bool: ...
    def _is_index_enabled(self) -> bool: ...
    def _read_current_premiums(self) -> dict[str, float]: ...
    def _update_view_strategy(self) -> None: ...
```

### 5.6 `strategies/{nifty50,banknifty}.py`
```python
class NIFTY50Strategy(StrategyInstance):
    index = "nifty50"

class BANKNIFTYStrategy(StrategyInstance):
    index = "banknifty"
```

### 5.7 `main.py`
```python
def main() -> None:
    enabled = read_enabled_indexes_from_redis()
    threads = []
    for idx in enabled:
        cls = {"nifty50": NIFTY50Strategy, "banknifty": BANKNIFTYStrategy}[idx]
        instance = cls(get_redis_sync(), load_index_config(idx))
        t = threading.Thread(target=instance.run, daemon=True, name=f"strategy_{idx}")
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
```

---

## 6. Order Execution Engine

### 6.1 `pre_entry_gate.py`
```python
def check(redis_sync, signal: Signal) -> tuple[bool, str]:
    """Returns (False, reason) on first failure. Reads only."""
```

### 6.2 `entry.py`
```python
def submit_and_monitor(redis_sync, signal: Signal, pos_id: str, broker_token: str) -> EntryResult:
    """DAY limit submission + monitor + modify/cancel logic per Strategy.md §9.4."""

@dataclass
class EntryResult:
    filled_qty: int
    avg_fill_price: float
    order_id: str
    order_events: list[dict]
    abandon_reason: str | None
```

### 6.3 `exit_eval.py`
```python
def evaluate(redis_sync, position: Position, current_premium: float) -> tuple[bool, str]:
    """Implements Strategy.md §7.9 priority order."""
```

### 6.4 `exit_submit.py`
```python
def submit_and_complete(redis_sync, position: Position, exit_reason: str, broker_token: str) -> ExitResult:
    """DAY limit + modify-only loop until filled. Cannot cancel."""
```

### 6.5 `reporting.py`
```python
def build_report(position: Position, entry: EntryResult, exit: ExitResult, snapshots: list[MarketSnapshot]) -> ClosedPositionReport
async def persist_report(postgres, report: ClosedPositionReport) -> str
```

### 6.6 `cleanup.py`
```python
def cleanup(redis_sync, pos_id: str, sig_id: str, order_ids: list[str], index: str) -> None:
    """EVALSHA cleanup_position.lua"""
```

### 6.7 `worker.py`
```python
def worker_loop(work_queue: queue.Queue, redis_sync, postgres) -> None:
    while True:
        signal = work_queue.get()
        try:
            process_signal(redis_sync, postgres, signal)
        except Exception as e:
            log.exception(f"worker failed on {signal.sig_id}: {e}")
        finally:
            work_queue.task_done()

def process_signal(redis_sync, postgres, signal: Signal) -> None: ...
```

### 6.8 `dispatcher.py`
```python
async def dispatcher_loop(redis_async, work_queue: queue.Queue) -> None:
    """XREADGROUP strategy:stream:signals; pulls signal_id, looks up payload, queues to worker pool, ACKs after worker reports."""
```

### 6.9 `main.py`
```python
def main() -> None:
    pool_size = config["execution"]["worker_pool_size"]
    work_queue = queue.Queue()
    for _ in range(pool_size):
        threading.Thread(target=worker.worker_loop, args=(work_queue, get_redis_sync(), get_pg_pool()), daemon=True).start()
    asyncio.run(dispatcher.dispatcher_loop(get_redis(), work_queue))
```

---

## 7. Background Engine

### 7.1 `position_ws.py`
```python
async def position_ws_loop(redis, broker_token: str) -> None:
    """Owns broker portfolio WS. On each event: HSET orders:broker:pos:{order_id} + XADD orders:stream:order_events."""
```

### 7.2 `pnl_computer.py`
```python
async def pnl_loop(redis, postgres, interval_sec: float = 1.0) -> None: ...
def compute_pnl(positions: list[Position], current_premiums: dict[str, float]) -> PnLSnapshot
```

### 7.3 `delta_pcr/compute.py`
```python
def compute_delta_pcr(
    current_oi: dict[int, dict[str, int]],
    previous_oi: dict[int, dict[str, int]],
) -> tuple[float, int, int]:
    """Returns (interval_pcr, total_d_put, total_d_call). Handles new-strike (previous=0) and exited-strike (ignore)."""
```

### 7.4 `delta_pcr/{nifty50,banknifty}_thread.py`
```python
def run(redis_sync, postgres) -> None:
    """Per-index thread. Wakes every 3 min from 09:18, fetches OI via option_chain.py, computes ΔPCR, writes Redis + Postgres."""
```

### 7.5 Other Threads
```python
async def token_refresh_loop(redis, interval_sec: int = 300) -> None
async def capital_poll_loop(redis, interval_sec: int = 30) -> None
async def kill_switch_poll_loop(redis, interval_sec: int = 30) -> None
```

### 7.6 `main.py`
Spawns all of the above.

---

## 8. Scheduler Engine

### 8.1 `tasks.py`
```python
TASKS: list[TaskDefinition] = [
    TaskDefinition("pre_open_snapshot",  cron="50 14 9 * * 1-5", event="pre_open_snapshot",  targets=["strategy"]),
    TaskDefinition("session_open",       cron="0 15 9 * * 1-5",  event="session_open",       targets=["strategy","data_pipeline"]),
    TaskDefinition("eod_squareoff",      cron="0 15 15 * * 1-5", event="eod_squareoff",      targets=["order_exec","strategy"]),
    TaskDefinition("session_close",      cron="0 30 15 * * 1-5", event="session_close",      targets=["all"]),
    TaskDefinition("graceful_shutdown",  cron="0 55 3 * * *",    event="graceful_shutdown",  targets=["all"]),
    TaskDefinition("instrument_refresh", cron="0 30 5 * * 1-5",  event="instrument_refresh", targets=["init","data_pipeline"]),
    TaskDefinition("token_refresh",      cron="0 5 4 * * *",     event="token_refresh",      targets=["background"]),
]
```

### 8.2 `main.py`
Cron scheduler loop firing `XADD system:stream:control` events.

---

## 9. Health Engine

### 9.1 `heartbeat_watcher.py`
```python
async def heartbeat_loop(redis, interval_sec: float = 1.0) -> None:
    """Reads system:health:heartbeats HASH every 1s, marks engines dead if last_hb_ts > 5s old. Updates system:health:engines."""
```

### 9.2 `dependency_probes.py`
```python
async def probe_redis(redis) -> DependencyStatus
async def probe_postgres(pool) -> DependencyStatus
async def probe_broker_auth(redis) -> DependencyStatus
async def probe_broker_kill_switch(redis) -> DependencyStatus
async def probe_loop(redis, pool, interval_sec: int = 10) -> None
```

### 9.3 `main.py`
Spawns watchers + view_builder for `ui:views:health`.

---

## 10. FastAPI Gateway

See `API.md` for full contract. Module structure:
```
api_gateway/
├── main.py              # FastAPI app construction
├── auth.py              # JWT issuance/verification
├── deps.py              # FastAPI dependencies
├── ws_endpoints.py      # /stream WS handler
├── view_router.py       # ui:pub:view → WS push
├── rest/
│   ├── auth.py
│   ├── configs.py
│   ├── strategy.py
│   ├── positions.py
│   ├── pnl.py
│   ├── delta_pcr.py
│   ├── health.py
│   ├── capital.py
│   └── commands.py
└── webhooks/
    └── upstox_token.py
```

---

## 11. Logging Setup

`backend/log_setup.py`:
```python
from loguru import logger
import sys

def configure(engine_name: str, level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stdout, format="{message}", serialize=True, level=level)
    logger.configure(extra={"engine": engine_name})

# every engine's main.py calls:
# from backend.log_setup import configure
# configure("data_pipeline")
```

---

## 12. Threading & Concurrency Summary

| Engine | Process | Threads |
|---|---|---|
| Init | 1 (one-shot) | 1 main |
| Data Pipeline | 1 | 1 ws_io + 1 tick_processor + 1 subscription_manager + 1 pre_market_subscriber + 1 view_builder |
| Strategy | 1 | 2 strategy threads (nifty50, banknifty) + 1 view_builder per index |
| Order Exec | 1 | 1 dispatcher (async) + 8 worker threads (pre-warmed) + 1 view_builder |
| Background | 1 | 1 position_ws + 1 pnl + 2 ΔPCR + 1 token_refresh + 1 capital_poll + 1 kill_switch_poll + 4 view_builders |
| Scheduler | 1 | 1 main loop |
| Health | 1 | 1 heartbeat watcher + 1 dependency prober + 1 view_builder |
| FastAPI | 1 | uvicorn workers (default 1) |

Each engine = 1 OS process. systemd manages start/stop/restart.

---

## 13. Implementation Order

1. `backend/state/` (redis_client, postgres_client, keys, streams, schemas)
2. `db/migrations/` Postgres DDL; run against local Postgres
3. `backend/log_setup.py`
4. `engines/init_engine/redis_template.py` (canonical schema)
5. `engines/init_engine/main.py` + holiday_check + auth_bootstrap + instruments_loader + strike_basket_builder
6. `engines/health_engine/`
7. `engines/data_pipeline_engine/`
8. `engines/strategy_engine/` (pure functions first, then base.py, then per-index modules)
9. `engines/background_engine/`
10. `engines/order_execution_engine/` (paper-mode lifecycle first; then live)
11. `engines/scheduler_engine/`
12. `engines/api_gateway/`
13. `deploy/systemd/` service units

---

## 14. Testing Strategy

- Pure functions get exhaustive unit tests (pytest)
- Engines get integration tests with dockerized Redis + Postgres
- No mocking of Redis/Postgres in tests — use real instances in test containers
- End-to-end paper-mode test: full system in paper mode against recorded broker WS replay
- Live smoke test: paper mode against real broker WS for one trading day

---

## 15. Cross-Engine Communication Contracts

Every coupling between engines must be one of these (no direct Python imports across engine boundaries):

| Producer | Channel/Stream | Consumer |
|---|---|---|
| Scheduler | `system:stream:control` | All engines |
| Data Pipeline | `market_data:indexes:{index}:*` Redis | Strategy, Order Exec (read directly) |
| Data Pipeline | `market_data:stream:tick:{index}` | Strategy.{index}, Order Exec |
| Strategy | `strategy:signals:{sig_id}` Redis JSON | Order Exec (via stream) |
| Strategy | `strategy:stream:signals` | Order Exec (consumer group "exec") |
| Strategy | `strategy:stream:rejected_signals` | (audit only) |
| Order Exec | `orders:positions:{pos_id}` Redis HASH | Background (PnL), FastAPI (views) |
| Order Exec | `orders:status:{pos_id}` Redis HASH | FastAPI (live progress) |
| Background | `orders:broker:pos:{order_id}` Redis HASH | Order Exec (entry/exit fill detection) |
| Background | `orders:stream:order_events` | Order Exec, FastAPI |
| Background | `orders:pnl:*` Redis | FastAPI (views) |
| Background | `strategy:{index}:delta_pcr:*` Redis | Strategy (veto modes), FastAPI |
| Health | `system:health:*` Redis, `ui:stream:health_alerts` | FastAPI |
| FastAPI | `orders:stream:manual_exit` | Order Exec |
| FastAPI | `strategy:configs:*` Redis writes | All engines (read at next event boundary) |
| Any | `ui:pub:view` channel + `ui:views:*` Redis | FastAPI (push to clients) |
| Any | `system:pub:system_event` | All engines |
