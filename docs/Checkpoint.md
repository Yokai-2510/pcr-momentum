# Session Checkpoint

Last updated: 2026-05-07 IST.

This file is the single handoff document. Anyone picking up the project
should read **this file first**, then `Project_Plan.md`, then `Strategy.md`,
then sibling docs as needed.

---

## 1. Where we are

**Backend is in production**, paper-mode, fully auto-cycled, with the **new
bid/ask imbalance order-flow strategy** as the only strategy.

| | |
|---|---|
| Public API | `https://3-6-128-21.sslip.io/api/` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream` |
| Health | `https://3-6-128-21.sslip.io/api/health` |
| Mode | `paper` |
| Active strategies | `bid_ask_imbalance_v1` × {nifty50, banknifty} |

The premium-diff momentum strategy has been removed entirely. Strategy code,
schema keys, configs, and database overlays specific to it are deleted. No
backwards-compat shims that affect runtime behavior — the bot is pre-revenue
and a clean cut was the right move.

---

## 2. The new strategy in one paragraph

`Strategy.md` is the spec. In short: a single `pcr-strategy` engine process
hosts N async **vessels**, one per `(strategy_id, instrument_id)` pair.
Each vessel is event-driven (Redis pub/sub on `tick.{token}` from the data
pipeline), reads 5-level market depth from the option_chain, computes 8
metrics (imbalance, spread, ask wall, aggressor, tick speed, cumulative,
net pressure, quality score), and applies a 4-gate entry sequence with
continuation + reversal-warning logic. The basket auto-shifts when spot
crosses a strike step (kills the stranded-basket failure observed in
premium-diff).

---

## 3. What changed in the recent refactor

**Schema** (`docs/Schema.md` for full reference)
- `strategy:registry` SET — active vessels (e.g. `bid_ask_imbalance_v1:nifty50`)
- `strategy:configs:strategies:{sid}` — strategy-level config (thresholds, time windows)
- `strategy:configs:strategies:{sid}:instruments:{idx}` — instrument-level (lot size, qty, SL%)
- `strategy:{sid}:{idx}:state | phase | basket | enabled | counters:* | metrics:*`
- `strategy:{sid}:{idx}:metrics:last_decision` + `last_decision_ts` — written every tick
- `market_data:pub:tick:{token}` — pub/sub channel for vessel wake-ups
- Option chain leaves now carry `bid_qtys[5]`, `ask_qtys[5]`, `bid_prices[5]`,
  `ask_prices[5]`, `total_bid_qty`, `total_ask_qty`

**Signal payload v2** — `strategy_id`, `instrument_id`, `score`, `score_breakdown`,
`net_pressure_at_signal`, `decision_ts` (legacy fields kept defaulted for one
release for the dispatcher; will be removed in Phase F.2).

**Code layout** under `backend/engines/strategy/`:
```
main.py           bootstrap (asyncio + uvloop)
runner.py         per-vessel async loop (all I/O, no strategy logic)
registry.py       discover vessels from strategy:registry SET
ingestion.py      single Redis pub/sub fan-out to vessels (TickRouter)
publisher.py      Action -> Signal v2 -> XADD strategy:stream:signals
heartbeat.py      per-vessel heartbeat (every 5 s)
observability/    decision_log + live_display (CMD-style block + WS push)

strategies/
  base.py                   Strategy Protocol (prepare/on_pre_open/on_tick/on_drain)
  bid_ask_imbalance/
    strategy.py             pure orchestrator
    basket.py               dynamic ATM management
    snapshot.py             typed Snapshot of basket state
    buffer.py               per-strike rolling tick history
    state.py                vessel state-machine helpers
    metrics/                8 modules — one per atomic metric
    decisions/              4 modules — gates, continuation, reversal, timing
```

Every metric and decision module is a pure function. The runner owns all I/O.
Vessels are fully independent — adding/removing one cannot affect siblings.

---

## 4. Daily lifecycle (unchanged from earlier)

- `pcr-start.timer` Mon-Fri 08:00 IST → `pcr-init.service` → bootstraps stack
- `pcr-stop.timer` Mon-Fri 15:45 IST → `pcr-stop.service` → graceful drain
- Persistent layer (Postgres + Redis + Nginx + FastAPI) runs 24×7

Strategy vessels detect the 15:30 IST session close internally and exit
cleanly. Init re-seeds the registry + default configs at next morning's
boot (idempotent — uses `SET NX` so operator-tuned configs are preserved).

---

## 5. What was verified live (2026-05-07)

```
strategy: starting
registry: discovered vessel bid_ask_imbalance_v1:nifty50
registry: discovered vessel bid_ask_imbalance_v1:banknifty
strategy: spawning 2 vessels
basket: initial_build added=22 dropped=0 atm=24350     (NIFTY)
basket: initial_build added=22 dropped=0 atm=56000     (BANKNIFTY)
vessel: session close reached       (correct — past 15:30 IST)
strategy: clean shutdown
```

Tomorrow's 08:00 IST auto-cycle will pick up the new code automatically and
run a full 09:15–15:30 session of the new strategy. First live verification
of metric flow + entry-gate behavior + dynamic ATM happens then.

---

## 6. What is deferred (with a clear path)

These are non-blocking — engine runs without them.

| Item | Where | When |
|---|---|---|
| Allocator Lua scripts namespaced by `(strategy_id, index)` | `state/lua/capital_allocator_*.lua` | Before adding 2nd strategy |
| Postgres `strategy_definitions` table + `strategy_id` column on trades | `alembic/versions/0010_*` | Before per-strategy PnL on dashboards |
| FastAPI `/strategy/status` v2 endpoint | `engines/api_gateway/rest/strategy.py` | When frontend integrates |
| Old back-compat shims in `state/keys.py` removed | `state/keys.py` (DEFAULT_STRATEGY_ID block) | After all 35+ call-sites refactored |

Existing back-compat shims map old `strategy:{idx}:*` helpers onto the new
`strategy:{sid}:{idx}:*` namespace under `DEFAULT_STRATEGY_ID =
"bid_ask_imbalance_v1"`. While only one strategy is active they are exact
equivalents.

---

## 7. Quick reference — operating it

```bash
# View live decisions
ssh -i ./nse_index_pcr_trading_pemkey.pem ubuntu@3.6.128.21 \
  "journalctl -u pcr-strategy -f"

# Read current vessel state
redis-cli -s /run/redis/redis.sock GET strategy:bid_ask_imbalance_v1:nifty50:state
redis-cli -s /run/redis/redis.sock GET strategy:bid_ask_imbalance_v1:nifty50:metrics:last_decision

# Hot-reload a config
curl -X PUT https://3-6-128-21.sslip.io/api/configs/strategy/bid_ask_imbalance_v1 \
  -H "Authorization: Bearer $JWT" \
  -d '{ ... }'

# Manual disable a vessel
redis-cli -s /run/redis/redis.sock SET strategy:bid_ask_imbalance_v1:nifty50:enabled false
```

---

## 8. Doc index

- [`Project_Plan.md`](./Project_Plan.md) — phases done + remaining
- [`Strategy.md`](./Strategy.md) — strategy spec (the new bid/ask imbalance)
- [`Schema.md`](./Schema.md) — Redis + Postgres reference
- [`HLD.md`](./HLD.md) — engine topology + hot-path discipline
- [`Sequential_Flow.md`](./Sequential_Flow.md) — daily lifecycle
- [`Modular_Design.md`](./Modular_Design.md) — module-level design
- [`TDD.md`](./TDD.md) — per-engine implementation contracts
- [`API.md`](./API.md) — REST + WS spec
- [`Frontend_Integration.md`](./Frontend_Integration.md) — frontend contract
- [`Dev_Setup.md`](./Dev_Setup.md) — operator runbook
