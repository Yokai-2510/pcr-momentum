# LLM Coding Guidelines — Premium-Diff Bot

This document defines the coding standards, structural conventions, and writing tone that any LLM (or human) contributing to this codebase must follow. Deviations require explicit justification in the commit message.

---

## 1. Code Structure

### 1.1 Function Per Task
Every distinct task is its own function. A function does one thing, named for what it does. Multi-step procedures are decomposed into named sub-functions, not inlined as long if/else chains.

Bad:
```python
def process(signal):
    if signal.mode == "live":
        # 80 lines of broker submission, monitoring, modify, cancel...
    else:
        # 60 lines of paper simulation...
```

Good:
```python
def process(signal):
    if signal.mode == "live":
        live_lifecycle(signal)
    else:
        paper_lifecycle(signal)

def live_lifecycle(signal):
    pos_id = create_position(signal)
    if not pre_entry_gate(signal):
        abort(pos_id)
        return
    entry_result = submit_and_monitor_entry(signal, pos_id)
    if not entry_result.filled:
        cleanup(pos_id)
        return
    monitor_until_exit(signal, pos_id)
    submit_and_complete_exit(signal, pos_id)
    finalize_and_persist(pos_id)
```

### 1.2 No Floating if/else Blocks
Conditional branches that contain non-trivial logic become functions with descriptive names. Inline `if/else` is reserved for short guards (≤3 lines per branch) and simple value selection.

### 1.3 Classes Only When State Justifies Them
Default to functions. Use a class only when the same state must be carried across multiple methods that operate on it together. Stateless utilities are functions, not class methods. Single-method classes (`class Foo: def run(self):`) are not allowed — that's a function.

Good use of class: `StrategyInstance` in `strategies/base.py` — has `index`, `strike_step`, `lot_size`, `state`, all needed across the lifecycle.

Bad use of class: `class PriceCalculator: def compute(...)` — that's a function `compute_price(...)`.

### 1.4 No Cluttered Code
- Maximum function length: ~60 lines. Beyond that, split.
- Maximum cyclomatic complexity: ~10 branches. Beyond that, refactor.
- Maximum nesting depth: 3 levels. Beyond that, extract a function or use early return.
- No module-level mutable state. No singletons except connection pools.

### 1.5 Early Returns Over Pyramid Nesting
Validate inputs and exit early. Reserve indented blocks for the happy path.

```python
# Good
def evaluate_entry(signal):
    if not signal.valid:
        return reject("invalid_signal")
    if not health_check_passes():
        return reject("health_failed")
    if not capital_available(signal):
        return reject("capital_insufficient")
    return accept_and_size(signal)
```

### 1.6 Pure Functions Where Possible
Logic that takes inputs and produces outputs without side effects goes in pure functions. Examples in this codebase: `compute_diffs`, `compute_sums`, `pick_highest_diff_strike`, `decide_when_flat`, `compute_delta_pcr`. These are unit-testable in isolation.

Side-effect functions (Redis writes, Postgres writes, broker calls) are kept thin — they assemble the call from already-computed pure-function outputs.

---

## 2. Engine-Like Architecture

### 2.1 Each Engine = One Process, One Responsibility
Engines run as independent OS processes managed by systemd. They communicate only via Redis Streams (durable events) and Redis Pub/Sub (fire-and-forget notifications). Engines never share Python objects across process boundaries. No `multiprocessing.Manager`, no shared memory dicts.

### 2.2 Single-Writer Rule per Redis Key Namespace
Every Redis key prefix has exactly one owner engine. Other engines may read freely; only the owner writes. This eliminates lock requirements and race conditions. Ownership is documented in `Schema.md` Section 5.

### 2.3 Stateless Engines
Engines do not hold state in Python memory between events. Every event handler reads what it needs from Redis, computes, writes back. A crashed engine restarts and resumes from Redis state with no recovery code required.

### 2.4 No Cross-Engine Imports of Business Logic
Engines may import `state/`, `brokers/`, and shared `schemas/`. They do not import each other's modules. Coupling happens through Redis stream contracts, never through Python imports.

---

## 3. Sequencing and Scheduling

### 3.1 Scheduler Owns All Time-Based Triggers
Time-based events (pre-open snapshot, market open, EOD, daily restart, instrument refresh) fire from the Scheduler Engine via `stream:control`. Engines do not check the wall clock to decide when to act on system-wide events. They subscribe to control-stream events and act when notified.

Per-position time computations (holding duration, time-exit threshold) are computed locally inside the worker thread that owns that position — those are position-scoped, not system-scoped.

### 3.2 No Conflicting Logic Across Engines
For every action, exactly one engine is responsible. Examples:
- Order submission: Order Exec (never Strategy, never Background)
- Position state writes: Order Exec (never Background)
- Broker portfolio fill detection: Background (never Order Exec polling broker)
- Signal generation: Strategy (never Order Exec inferring)
- View key rebuilding: the engine that owns the underlying state

If two engines could both plausibly do something, the design is wrong. Resolve by reassigning ownership, not by adding coordination logic.

### 3.3 Sequential Flow per Position
A position moves through stages strictly in order, each stage written to `status:order:{pos_id}`:

```
GATE_PREENTRY → ENTRY_SUBMITTING → ENTRY_FILLED →
EXIT_EVAL → EXIT_SUBMITTING → EXIT_FILLED →
REPORTING → CLEANUP → DONE
```

No stage skipping. No re-entry into an earlier stage. Each transition is monotonic and persisted.

### 3.4 Daily Lifecycle is Deterministic
The 04:00 IST cycle (graceful shutdown → Init → engine startup in dependency order) is the only acceptable mechanism for system reset. No hot-reload of configs that would invalidate in-flight state. Config edits via FastAPI are write-through to Postgres + Redis but engines pick them up only at the next event boundary or restart.

---

## 4. Schema Discipline

### 4.1 Schema Lives in `Schema.md` and `state/schemas/`
The Redis schema and Postgres schema are documented in `Schema.md`. Pydantic models in `state/schemas/` are the runtime enforcement. Every inter-engine message and every view payload is a typed pydantic model.

When introducing a new key or table column, update `Schema.md` and the relevant pydantic model in the same commit. Code that writes/reads schema-bearing data must reference the pydantic model, not raw dict literals.

### 4.2 Categorized and Namespaced
Redis keys use colon-separated namespaces grouped by category (system, heartbeat, config, cred, instruments, market data, strategy, signal, order, position, capital, health, view, stream, pub). Each category has a single owner engine.

Postgres tables grouped by purpose-prefix: `user_*` (identity, credentials, audit), `config_*` (settings, task definitions), `market_*` (calendar, instruments cache), `trades_*` (closed positions, rejected signals), `metrics_*` (time-series append-only — order events, pnl, delta_pcr, health, system events).

### 4.3 Naming Conventions
- All Redis keys lowercase, colon-separated
- Index identifier always `nifty50` / `banknifty` (lowercase, no underscores between words, no spaces)
- View keys always `view:{name}`, singular for single-record views, plural for collections
- Streams always `stream:{name}`; per-index streams suffix with `:{index}`
- Pub/Sub channels always `pub:{name}`
- Postgres tables snake_case plural (`closed_positions`, not `ClosedPosition`)
- Postgres columns snake_case (`entry_ts`, not `EntryTs`)
- Python identifiers snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants

### 4.4 Configuration is Data, Not Code
All tunable values live in `config:*` Redis keys mirrored from Postgres `configs`. Code reads from `config:*` at the point of use; never hardcode thresholds, timeouts, lot sizes, buffer values, or any business parameter inside function bodies. Defaults exist in `redis_template.py` only.

---

## 5. Comments and Documentation Tone

### 5.1 Objective Voice
Comments and docstrings describe what the code does. They do not address the reader, do not reference past conversations, do not say "as requested" or "per the spec." They state facts about the code.

Bad:
```python
# As you specified, we wait 5 seconds before evaluating.
def wait_before_first_tick():
    time.sleep(5)

# This is the function you wanted for the reversal flip logic.
def reversal_flip(...):
    ...
```

Good:
```python
# Settle window: no signal emission 09:15:00–09:15:09 to filter auction-crossover noise.
def wait_settle_window_end():
    ...

def reversal_flip(...):
    """Close current position and open opposite-side position when SUM_PE − SUM_CE exceeds the reversal threshold."""
    ...
```

### 5.2 Comment Only the Non-Obvious
- Do not comment what the code already says (`# increment counter` above `counter += 1`)
- Do comment the why when it's a non-obvious constraint, hidden invariant, or workaround for a specific market mechanic
- Do comment magic numbers if they cannot be moved to config (rare — most should be in config)

### 5.3 Docstrings
Public functions get docstrings. One-line summary first, then parameters and return shape if non-trivial. No multi-paragraph essays. No Greek-letter formula derivations — those go in `Strategy.md`.

Bad:
```python
def compute_sums(diffs, ce_strikes, pe_strikes):
    """
    This is a function I made to compute the sums.
    It takes diffs and the strikes and returns sums.
    Hope this helps!
    """
```

Good:
```python
def compute_sums(diffs: dict, ce_strikes: list, pe_strikes: list) -> tuple[float, float]:
    """Aggregate per-strike diffs into SUM_CE and SUM_PE per Strategy.md §5.1."""
```

### 5.4 No Apologetic or Self-Referential Code
No "TODO: this is hacky", no "// I know this is bad but...", no "// AI-generated, please review". Code submitted is code stood behind. If something is genuinely temporary, use `# TEMPORARY:` with a tracked issue ID.

### 5.5 No First-Person, No Acknowledgments
Comments and commit messages do not use "I", "we", "you", or "the user". They describe the system. Commit messages name the change made, not the journey to making it.

Bad commit: `"Added the function the user asked for to handle ΔPCR computation as we discussed."`
Good commit: `"Add per-index ΔPCR computation thread with 3-minute interval cadence."`

---

## 6. Error Handling

### 6.1 Fail Loudly at Boundaries, Trust Internally
At system boundaries (broker API responses, FastAPI request bodies, Redis reads of optional keys), validate explicitly and raise typed exceptions. Inside the system, trust that pydantic-validated data is well-formed; do not double-check.

### 6.2 No Bare `except`
Catch specific exceptions. `except Exception:` only at thread/process top-level wrappers where the alternative is silent crash.

### 6.3 No Default Values That Mask Bugs
`dict.get("key", default)` is allowed only when missing key is a legitimate state. If missing means "broken", use `dict["key"]` and let the KeyError surface.

### 6.4 Retry Logic Is Bounded and Logged
Every retry has a max count and exponential backoff. Every retry attempt is logged with the failure reason. Retry without bound is forbidden.

---

## 7. Logging

### 7.1 Library and Sink
Use `loguru` (configured in `backend/log_setup.py`). Emit structured JSON to stdout. systemd journald captures stdout per service; query via `journalctl`. **No external aggregator** — no Loki, no Promtail, no ELK.

### 7.2 Required Fields per Line
Every log line is one JSON object: `ts`, `level`, `engine`, `module`, `msg`. Recommended: `sig_id`, `pos_id`, `order_id`, `index` when relevant.

### 7.3 Log Levels
- `DEBUG` — verbose state transitions; off in production by default
- `INFO` — significant lifecycle events (signal emit, order submit, position close, engine start/stop)
- `WARNING` — recoverable issues (broker WS reconnect, retry, config drift)
- `ERROR` — operation aborted but engine survives
- `CRITICAL` — engine-level fatal; systemd restarts

### 7.4 Latency Tracking via Logs (No Prometheus)
For per-trade latency, write fields directly into `trades_closed_positions.latencies` JSONB at trade close. For engine-level timing visibility, emit timed log entries:
```python
logger.info("event_processed", event="overtake", duration_ms=12)
```
Aggregate via SQL on `trades_closed_positions.latencies`, or `journalctl ... | jq 'select(.duration_ms != null)'` for ad-hoc analysis.

### 7.5 No PII or Secrets in Logs
Broker tokens, API keys, passwords, TOTP secrets — masked at the source (first 6 + last 4 chars). Personal identifiers never appear in logs.

---

## 8. Testing

### 8.1 Pure Functions Get Exhaustive Unit Tests
Every pure function in `strategy_engine/`, `delta_pcr/compute.py`, `pre_entry_gate.py`, `exit_eval.py`, `decision.py`, `premium_diff.py` has a `test_*.py` file with edge cases (zero, negative, boundary, both-positive, both-negative, exact threshold).

### 8.2 Engines Get Integration Tests with Real Redis + Real Postgres
Engine tests spin up dockerized Redis + Postgres, run the engine against scripted Redis stream inputs, assert on Redis state writes and Postgres rows created.

### 8.3 No Mocking of Redis or Postgres
Mock-based tests of stateful engines lie. Use real instances in test containers.

### 8.4 End-to-End Paper-Mode Test
Full system in paper mode against a recorded broker WS replay. Compare actual closed_positions output against expected.

---

## 9. Performance Discipline

### 9.1 Hot-Path Rules
On the tick processing path (Data Pipeline → Strategy → Order Exec):
- Use `orjson` for all serialization, never stdlib `json`
- Every multi-key Redis op uses `pipeline()` or Lua — single round-trip
- No `KEYS *`, no unbounded `SCAN` — maintain index sets if you need to enumerate
- Redis on Unix socket, never TCP localhost
- No `time.sleep` on hot paths — use `XREAD BLOCK 0`, `asyncio.Event`, or `asyncio.sleep(0)`

### 9.2 No Premature Optimization
Cold paths (config UI, EOD report generation, history queries) optimize for readability. Hot paths optimize for latency. Do not micro-optimize cold paths.

### 9.3 Profile Before Optimizing
Latency claims are measured with `prometheus_client.Histogram` in production, not assumed.

---

## 10. Dependencies

### 10.1 Pinned Versions
`requirements.txt` pins exact versions (`==`, not `>=`). `pip-tools` or `uv` resolves transitive dependencies into a lockfile.

### 10.2 Conservative Dependency Additions
Adding a new dependency requires justification. Standard library first; well-known well-maintained packages (pydantic, redis, asyncpg, fastapi, websockets, orjson, uvloop, loguru, prometheus_client, opentelemetry, pytest) are pre-approved. Anything else needs review.

---

## 11. Documentation Sync Rule

### 11.1 Code and Docs Update Together
Any code change that affects:
- A Redis key shape → update `Schema.md` §1
- A Postgres table → update `Schema.md` §2 and add a migration in `db/migrations/`
- A pydantic schema → update the model AND `Schema.md` §4
- An engine's behavior → update `TDD.md` and `HLD.md` §4
- A strategy rule (threshold, decision logic, exit condition) → update `Strategy.md`
- A lifecycle timing, boot gate, or drain ordering → update `Sequential_Flow.md`
- A frontend contract change (view payload shape, WS protocol, REST endpoint) → update `API.md` and `Frontend_Basics.md`
- A new operational threshold or config knob → update `Strategy.md` §14 and `Schema.md` §3

The commit must include both code and doc changes. Doc-out-of-sync PRs are rejected.

### 11.2 Doc Updates Required at the End of Every Task
After completing any non-trivial change to the codebase, scan the seven core docs (`Strategy.md`, `Sequential_Flow.md`, `HLD.md`, `TDD.md`, `Schema.md`, `API.md`, `Frontend_Basics.md`) and update any sections that no longer reflect the code. This is part of the task, not a follow-up.

---

## 12. What Not to Do

- Do not introduce a new Redis key that has no documented owner
- Do not add a hardcoded threshold or magic number in a function body — push it to config
- Do not create a new long function instead of decomposing
- Do not add a class wrapping a single function
- Do not add a comment that says "as the user wants" or any acknowledgment of the conversation that produced the code
- Do not write code that requires the reader to know external context not present in this repo
- Do not skip writing tests for new pure functions
- Do not add a dependency without justification
- Do not silence exceptions to make tests pass
- Do not add `try/except Exception: pass` anywhere
- Do not write to a Redis key from a non-owner engine
- Do not poll the broker for fill status — read `pos:{order_id}` from Background's Portfolio WS writes
- Do not use stdlib `json` on the hot path
- Do not use `time.sleep` in event-driven paths

---

## 13. Type Hints Are Mandatory

Every function signature carries type hints. Every public function returns a typed value (or `None`). Use `from __future__ import annotations` at the top of every file. Use `typing.Literal` for enum-like string args. Use `typing.TypedDict` or pydantic models for dict-shaped args.

Bad:
```python
def submit_order(signal, lots, price):
    ...
```

Good:
```python
def submit_order(signal: Signal, lots: int, price: float) -> EntryResult:
    ...
```

Run `mypy --strict` in CI. Type errors block merge.

---

## 14. Async vs Sync Discipline

- Engines that own asyncio loops (Init, Data Pipeline, Strategy main, Order Exec dispatcher, Background, Scheduler, Health, API Gateway) use async Redis client (`redis.asyncio`).
- Worker threads inside Order Exec use the sync Redis client.
- Never mix: an async function never calls a sync Redis method; a thread worker never calls an async coroutine without `asyncio.run_coroutine_threadsafe`.
- Use `uvloop` as the event loop policy in every async engine — set in `main.py` before any `asyncio.run`.

---

## 15. Configuration Reads Are Cheap, Cache Sparingly

`config:*` keys are small JSON blobs. Engines read them on demand at the point of use rather than caching them at startup. The exception: hot-path engines (Order Exec worker on every signal, Strategy on every tick) cache the config object once per signal/tick to avoid repeated round-trips. Cache invalidation happens at the natural event boundary (next signal, next tick).

Never read `config:*` inside a tight inner loop without a per-iteration cache.

---

## 16. Float Arithmetic for Money Is Acceptable Here

Trading premiums are floats with paisa precision (₹0.05). `Decimal` arithmetic is overkill for this bot's accuracy needs and slow on the hot path. Use `float` for premium math; round to 2 decimals for display and Postgres storage; use `numeric` Postgres type for monetary columns.

What's not acceptable: comparing floats for equality (`==`). Use threshold comparisons (`abs(a - b) < 0.005`) when needed.

---

## 17. Time Handling

- All timestamps stored as UTC in Postgres (`TIMESTAMPTZ`)
- All timestamps in Redis stored as Unix epoch ms (`int`) when used for computation, ISO-8601 strings when used for display
- All wall-clock comparisons in trading logic use `datetime.now(IST)` where `IST = ZoneInfo("Asia/Kolkata")`
- Never use `datetime.now()` without an explicit timezone
- Market session times live in `config:session` and are read from there, not hardcoded in code

---

## 18. Imports Are Sorted and Grouped

Use `isort` with the standard layout:
1. Standard library
2. Third-party packages (alphabetical)
3. Local imports (alphabetical, by module path)

No wildcard imports (`from module import *`) anywhere. No circular imports — if you need one, the design is wrong.

---

## 19. File Headers

Every Python module starts with a short module-level docstring stating its responsibility, in objective voice:

```python
"""Premium-difference momentum computation. Pure functions, no I/O."""
```

Not:
```python
"""
This module is for computing the premium difference.
I made this for the user as part of the strategy engine.
TODO: clean this up later.
"""
```

---

## 20. Inter-Engine Contract Changes

Adding or modifying a Redis key, stream, Pub/Sub channel, or Postgres column is a breaking change. The change requires:

1. Schema doc updated (`Schema.md`)
2. Pydantic model updated (`state/schemas/`)
3. Owner engine updated to write the new shape
4. Consumer engines updated to read the new shape
5. Migration plan if affecting durable state (Postgres or persistent Redis keys)
6. Init Engine's `redis_template.py` updated for new defaults

All five must land in the same commit or coordinated PR set. Partial changes break the system.

---

## 21. Backwards Compatibility Within a Single User Deployment

This system is single-user, single-deployment. Aggressive refactoring is allowed — there is no external API to maintain compatibility with. Database migrations may drop columns or rename tables without deprecation cycles. Frontend updates ship together with backend updates. Don't add backwards-compatibility shims for hypothetical future users.

---

## 22. Don't Output to stdout in Production

`print()` is forbidden in committed code. All output goes through the configured logger. Tests may use `print()` for debug; CI strips them via lint rule before merge.

---

## 23. Recommended Reading Order for a New Contributor

1. `Strategy.md` — what the bot does (edge, state machine, entry/exit rules)
2. `Sequential_Flow.md` — daily lifecycle, boot/drain, readiness gates, recovery
3. `HLD.md` — topology, engines, streams, frontend contract, hot-path discipline
4. `Schema.md` — Redis + Postgres data contracts
5. `Modular_Design.md` — module breakdown with function signatures
6. `API.md` — FastAPI REST + WebSocket contract
7. `Frontend_Basics.md` — push-only protocol, view keys, reconnect, recommended stack
8. `Dev_Setup.md` — EC2 / Ubuntu / Nginx / systemd setup + first-boot credential bootstrap design
9. `TDD.md` — engine-level implementation details
10. This document — coding standards
11. Source code, starting with `state/` then `engines/init_engine/` then any other engine

A new contributor should be productive in ~1 day with this set.
