# Session Checkpoint

Last updated: 2026-05-10 IST.

This is the single handoff document. Any LLM resuming work should read this
first, then `Project_Plan.md`, then `Strategy.md`, then sibling docs as
needed.

The first two sections describe what is currently deployed and where the
system is going. Section 3 is the in-flight refactor plan with per-step
status. Sections 4-7 are reference material.

---

## 1. Current deployment

| | |
|---|---|
| Public API | `https://3-6-128-21.sslip.io/api/` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream` |
| Health (no auth) | `https://3-6-128-21.sslip.io/api/health` |
| Mode | `paper` (broker calls return paper fills) |
| Active strategies | `bid_ask_imbalance_v1` × {nifty50, banknifty} |
| Trading window | Mon–Fri 09:15–15:30 IST |
| Init timer | `pcr-start.timer` Mon–Fri 02:30 UTC = 08:00 IST |
| Stop timer | `pcr-stop.timer` Mon–Fri 10:15 UTC = 15:45 IST |
| EC2 host | `ubuntu@3.6.128.21` (key `nse_index_pcr_trading_pemkey.pem` in repo root) |
| Repo on EC2 | `/home/ubuntu/premium_diff_bot/repo` (origin: `git@github.com:Yokai-2510/pcr-momentum.git`) |
| Python | 3.12 venv at `/home/ubuntu/premium_diff_bot/.venv` |
| Redis | unix socket `/run/redis/redis.sock`, no persistence, 1 GB LRU cap |
| Postgres | `premium_diff_bot` DB, owner `trader` |

Persistent layer (24×7): redis, postgres, nginx, `pcr-api-gateway`.
Cyclic layer (08:00→15:45 weekdays): init, data-pipeline, strategy,
order-exec, background, scheduler, health.

Most recent commits on `main`:

```
6b2f60d  fix: complete EXIT signal flow + table-driven cooldowns + held-token pinning
1257be6  fix(state): close the position-→-vessel state-write loop
475fa68  fix(strategy): runner no longer writes vessel state — order-exec is the sole writer
d7f3c3f  docs: strip migration/comparison narrative from spec docs
353fb31  feat(strategy): replace premium-diff with bid/ask imbalance order-flow strategy
```

---

## 2. Architecture target

Target topology (the in-flight refactor moves us toward this):

```
data pipeline           : 1 universal process (already there)
strategy engine         : 1 OS process PER strategy_id  ←  Step 2
                          each process hosts vessels (one per instrument)
                          each vessel = (calculations + signal generation)
                            calculations  → metrics/* (pure functions)
                            signal gen    → decisions/* (pure functions)
order execution engine  : 1 process, dynamically sized worker pool   ←  Step 4
                          worker_pool_size = Σ(max_parallel_positions) + 1
                          each worker runs one position's full A→F lifecycle
                          OR (post Phase 7.1) entry-handler workers + 1 monitor
allocator               : per-vessel (1) + per-strategy (configurable) +
                          global (Σ + buffer) atomic gates
schema                  : minimal — only keys with active readers
no lua                  : pure Python pipelines / transactions          ←  Step 1
```

---

## 3. In-flight refactor — six steps

### Status legend
- `[done]`  finished and committed
- `[wip]`   in progress this session
- `[next]`  not started, ordered for next pickup
- `[skip]`  decided against / out of scope

### Step 1 — Drop Lua scripts, replace with Python   `[done]` (commit `9564ac4`)

Replaced both atomic-cleanup and atomic-allocator Lua scripts with plain
Python.

`allocator.py` uses `redis.transaction()` (WATCH/MULTI/EXEC with bounded
retry) for check-then-mutate. `cleanup.py` uses a sequential
`pipeline(transaction=False)` since the cleanup thread is the single owner
of the position keys.

Removed: `backend/state/lua/` (entire dir), `redis_client.load_script`,
`redis_client.clear_script_cache`, `tests/test_lua.py`,
`tests/test_redis_client.py::TestLuaLoader`.

Verified live on EC2: full allocator round-trip (reserve/duplicate-reject/
release/different-index-reserve) returns the expected results. Order-exec
restarted clean.

### Step 2 — Per-strategy OS isolation   `[done]` (commit `ae1b98c`)

Each strategy runs in its own OS process via systemd template unit
`pcr-strategy@.service` parameterized by `%i = strategy_id`.

**Adding a new strategy** (operator workflow):

1. Author `Strategy` subclass under `engines/strategy/strategies/<name>/`
2. INSERT into Postgres `strategy_definitions`; `SADD strategy:registry "<sid>:<idx>"` for each instrument
3. Append `Wants=pcr-strategy@<sid>.service` to `scripts/systemd/pcr-stack.target`,
   re-run `scripts/systemd/install.sh`, `sudo systemctl daemon-reload`
4. `sudo systemctl enable --now pcr-strategy@<sid>.service`

**Verified live on EC2**: `pcr-strategy@bid_ask_imbalance_v1.service` boots
clean, registry filter picks only the two matching vessels, basket build
runs, FRESH_ENTRY signals emit. Heartbeat field is `strategy:bid_ask_imbalance_v1`,
engine_up flag is `engine_up:strategy:bid_ask_imbalance_v1`.

Health probe (`probe_engines`) is generic — iterates all heartbeat fields
— so the per-strategy heartbeat shows up automatically in `/api/health`.

### Step 3 — Schema simplification   `[next]`

**Goal**: remove keys that have no consumer, simplify Signal payload.

**Drop**:
- `strategy:signals:counter` (incremented, never read)
- `strategy:signals:active` SET (added on emit, never cleaned — useless)
- `strategy:{sid}:{idx}:phase` + `phase_entered_ts` (written, never consumed)
- `system:flags:graceful_shutdown_initiated` (set once, no consumer)
- `Signal.strategy_version` field (deprecated duplicate of `strategy_id`)
- `Signal.diff_at_signal`, `sum_ce_at_signal`, `sum_pe_at_signal`,
  `delta_at_signal`, `delta_pcr_at_signal` (premium-diff legacy, all default-zero)

**Keep** (these earn their existence):
- `vessel:state`, `vessel:current_position_id`, `vessel:cooldown_until_ts`, `vessel:cooldown_reason`
- `vessel:basket`, `vessel:enabled`
- `vessel:counters:{entries_today, wins_today, reversals_today}`
- `vessel:metrics:{net_pressure, cum_ce_imbalance, cum_pe_imbalance, per_strike, last_decision, last_decision_ts}`

**Files to change**:
- `backend/state/keys.py` — remove the dead helpers
- `backend/state/schemas/signal.py` — drop deprecated fields
- `backend/engines/init/redis_template.py` — remove dropped keys from template
- `backend/engines/strategy/runner.py` — stop writing `phase` / `phase_entered_ts`
- `backend/engines/strategy/publisher.py` — stop populating dropped Signal fields
- `backend/engines/order_exec/dispatcher.py` — stop reading dropped Signal fields
- `docs/Schema.md` — sync the namespace tables

**Acceptance**:
- `redis-cli --scan --pattern 'strategy:signals:active'` returns empty after init
- No code path references `Signal.strategy_version`
- Schema.md tables match what's actually in Redis

### Step 4 — Per-strategy parallelism config + dynamic worker pool   `[next]`

**Goal**: each strategy declares its own `max_parallel_positions`. Order-exec
sizes its worker pool based on the sum across registered strategies.

**Schema additions**:
- `strategy:configs:strategies:{sid}.max_parallel_positions` (int, default 1)
- New allocator keys (already partially present in keys.py):
  - `orders:allocator:open:{sid}:{idx}` (int — per-vessel slot count, max 1)
  - `orders:allocator:open_by_strategy:{sid}` (int — per-strategy slot count,
    capped at `max_parallel_positions`)

**Files to change**:
- `backend/engines/order_exec/allocator.py` — three-tier check
  (vessel → strategy → global) in the post-Lua Python implementation
- `backend/engines/order_exec/main.py` — at startup, sum max_parallel across
  registry to compute pool size
- `backend/engines/order_exec/pre_entry_gate.py` — pass `strategy_id` through
- `backend/state/keys.py` — finalize the per-strategy allocator keys

**Acceptance**:
- Two strategies with `max_parallel=1` each → order-exec spawns `2+1=3` worker threads
- Strategy A holding a NIFTY position cannot prevent Strategy B from opening one on NIFTY
- Allocator rejection reason explains which cap was hit

### Step 5 — Phase 7.1: split worker into entry-handler + monitor   `[next]`

**Goal**: order-exec restart no longer strands open positions.

**Architectural change**:

```
TODAY:  process_signal()  does pre_entry_gate → entry → MONITOR LOOP → exit_submit → cleanup
TARGET: process_entry_signal()  does pre_entry_gate → entry → write monitor-context → return
        monitor_loop()  scans orders:positions:open → reads monitor-context
                        → runs exit_eval → exit_submit → cleanup → state transition
```

**Files to change**:
- New: `backend/engines/order_exec/monitor.py`
- New: `backend/engines/order_exec/exit_pipeline.py` (extracted Stage D-F)
- `backend/engines/order_exec/worker.py` — strip Stage D-F, persist
  monitor-context blob (`orders:positions:{pos_id}:context` JSON) after
  Stage C, then return
- `backend/engines/order_exec/main.py` — spawn monitor thread alongside
  worker pool

**Monitor-context blob shape**:
```json
{
  "entry_result": { ... entry_mod result fields ... },
  "signal_snapshot": { ... full Signal as JSON ... },
  "market_snapshot_entry": { ... },
  "pre_open_snapshot": { ... },
  "premium_reserved": 17171.25
}
```

**Acceptance**:
- `systemctl restart pcr-order-exec` while a paper position is open: monitor adopts it on restart, continues exit-eval, eventually closes via SL/target/EOD
- Live PnL on `orders:positions:{pos_id}.pnl` updates while order-exec is running, no matter which signal opened it

### Step 6 — Small bug fixes + housekeeping   `[next]`

- `redis_template.py`: seed `orders:pnl:day` as `"0"` (currently absent → reads return None)
- `pcr-scheduler.service`: add `Restart=on-failure` so SIGTERM-mid-cycle deaths self-heal
- `pcr-strategy@.service` template: same `Restart=on-failure`
- After cleanup runs, ensure `vessel:counters:wins_today` is incremented inside the cleanup transaction (today it's two writes that could de-sync)
- Update `docs/HLD.md` §4.3 — vessel architecture (template-unit per strategy)
- Update `docs/Modular_Design.md` — drop Lua references

---

## 4. Known issues / open observations

| # | Where | Severity | Note |
|---|---|---|---|
| 1 | `redis_template` | low | `pnl:day` default missing, reads return None |
| 2 | `pcr-scheduler.service` | low | died with `failed` status on Friday's drain (SIGTERM mid-cycle); restarts clean Monday but unit needs `Restart=on-failure` |
| 3 | `signals:active` SET | medium | added on emit, never SREM'd — set grows unbounded within a session, gets DEL'd by next init's flush. Just remove the SADD entirely |
| 4 | rapid re-entry storm | resolved | Friday saw 2123 entries on NIFTY. Fixed by `STRATEGY_EXIT` cooldown table in commit 6b2f60d. Confirm Monday |
| 5 | held-token outside basket | resolved | snapshot now pins held leg (commit 6b2f60d) |
| 6 | broker WS reconnect resilience | open | Thursday's session: WS dropped silently, didn't recover. Worth digging in `backend/engines/data_pipeline/ws_io.py` reconnect callbacks |
| 7 | order-exec restart strands positions | known, deferred to Step 5 | not a bug today, will manifest if order-exec restarts mid-session |

---

## 5. Doc index

- [`Project_Plan.md`](./Project_Plan.md) — phase status; Phase 7.1 documented as architectural debt
- [`Strategy.md`](./Strategy.md) — strategy spec (bid/ask imbalance, vessels, metrics, decisions)
- [`Schema.md`](./Schema.md) — Redis + Postgres reference (Step 3 will trim this)
- [`HLD.md`](./HLD.md) — engine topology + hot-path discipline (Step 2 will rewrite §4.3)
- [`Sequential_Flow.md`](./Sequential_Flow.md) — daily lifecycle
- [`Modular_Design.md`](./Modular_Design.md) — module-level reference
- [`TDD.md`](./TDD.md) — per-engine implementation contracts
- [`API.md`](./API.md) — REST + WS spec
- [`Frontend_Integration.md`](./Frontend_Integration.md) — frontend contract
- [`Dev_Setup.md`](./Dev_Setup.md) — operator runbook

---

## 6. Operating commands (quick reference)

```bash
# SSH in
ssh -i ./nse_index_pcr_trading_pemkey.pem ubuntu@3.6.128.21

# Restart a single engine
sudo systemctl restart pcr-strategy.service

# Watch live decisions
journalctl -u pcr-strategy -f

# Read vessel state
redis-cli -s /run/redis/redis.sock GET strategy:bid_ask_imbalance_v1:nifty50:state
redis-cli -s /run/redis/redis.sock GET strategy:bid_ask_imbalance_v1:nifty50:metrics:last_decision

# Force-disable a vessel
redis-cli -s /run/redis/redis.sock SET strategy:bid_ask_imbalance_v1:nifty50:enabled false

# Hot-reload a config
curl -X PUT https://3-6-128-21.sslip.io/api/configs/strategy/bid_ask_imbalance_v1 \
  -H "Authorization: Bearer $JWT" -d '{ ... }'

# Pull current backend tree from EC2 to local
ssh -i ./nse_index_pcr_trading_pemkey.pem ubuntu@3.6.128.21 \
  'cd /home/ubuntu/premium_diff_bot/repo && tar czf - --exclude=__pycache__ backend' \
  > /tmp/backend.tgz && tar xzf /tmp/backend.tgz -C .
```

---

## 7. How an LLM resumes work from this checkpoint

1. Read this file end-to-end.
2. Read `Strategy.md` (the strategy spec) and `Schema.md` (current schema).
3. Pick the next `[next]` step from §3 in order.
4. Before changing any file, run the "Files to change" list against `git log
   --oneline -- <path>` to see if anything has shifted since this checkpoint
   was written.
5. Sync EC2 backend tree to local (last command in §6) so file edits land
   on a current copy.
6. Implement the step's scope. Verify against the step's "Acceptance" list.
7. Commit with a focused message; push to `main` (no PRs — see CLAUDE.md
   memory `feedback_no_prs`).
8. Update the corresponding step's status in this file from `[next]` to
   `[done]`. Move the next step to `[wip]` if continuing in the same session.

If a step's Acceptance can't be met because of unrelated breakage, write a
new entry in §4 (Known issues) describing what was found, and continue
with the next step.
