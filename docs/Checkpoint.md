# Session Checkpoint

Last updated: 2026-05-07 IST.

Read this first, then `Project_Plan.md`, then `Strategy.md`, then sibling docs as needed.

---

## 1. Deployment

| | |
|---|---|
| Public API | `https://3-6-128-21.sslip.io/api/` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream` |
| Health | `https://3-6-128-21.sslip.io/api/health` |
| Mode | `paper` |
| Active strategies | `bid_ask_imbalance_v1` × {nifty50, banknifty} |

---

## 2. Strategy

`Strategy.md` is the spec. One `pcr-strategy` engine process hosts N async **vessels**, one per `(strategy_id, instrument_id)` pair. Each vessel is event-driven (Redis pub/sub on `tick.{token}` from the data pipeline), reads 5-level market depth from the option_chain, computes 8 metrics (imbalance, spread, ask wall, aggressor, tick speed, cumulative, net pressure, quality score), and applies a 4-gate entry sequence with continuation + reversal-warning logic. The basket auto-shifts when spot crosses a strike step.

---

## 3. Schema reference

Full reference in `Schema.md`. Highlights:

- `strategy:registry` SET — active vessels (e.g. `bid_ask_imbalance_v1:nifty50`)
- `strategy:configs:strategies:{sid}` — strategy-level config (thresholds, time windows)
- `strategy:configs:strategies:{sid}:instruments:{idx}` — instrument-level (lot size, qty, SL%)
- `strategy:{sid}:{idx}:state | phase | basket | enabled | counters:* | metrics:*`
- `strategy:{sid}:{idx}:metrics:last_decision` + `last_decision_ts` — written every tick
- `market_data:pub:tick:{token}` — pub/sub channel for vessel wake-ups
- Option chain leaves carry `bid_qtys[5]`, `ask_qtys[5]`, `bid_prices[5]`,
  `ask_prices[5]`, `total_bid_qty`, `total_ask_qty`

---

## 4. Code layout under `backend/engines/strategy/`

```
main.py           bootstrap (asyncio + uvloop)
runner.py         per-vessel async loop (all I/O, no strategy logic)
registry.py       discover vessels from strategy:registry SET
ingestion.py      single Redis pub/sub fan-out to vessels (TickRouter)
publisher.py      Action -> Signal -> XADD strategy:stream:signals
heartbeat.py      per-vessel heartbeat (every 5 s)
observability/    decision_log + live_display

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

Every metric and decision module is a pure function. The runner owns all I/O. Vessels are independent — adding/removing one cannot affect siblings.

---

## 5. Daily lifecycle

- `pcr-start.timer` Mon-Fri 08:00 IST → `pcr-init.service` → bootstraps stack
- `pcr-stop.timer` Mon-Fri 15:45 IST → `pcr-stop.service` → graceful drain
- Persistent layer (Postgres + Redis + Nginx + FastAPI) runs 24×7

Strategy vessels detect the 15:30 IST session close internally and exit cleanly. Init re-seeds the registry + default configs at next morning's boot (idempotent — uses `SET NX` so operator-tuned configs are preserved).

---

## 6. Quick reference — operating it

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

## 7. Doc index

- [`Project_Plan.md`](./Project_Plan.md) — phases done + remaining
- [`Strategy.md`](./Strategy.md) — strategy spec
- [`Schema.md`](./Schema.md) — Redis + Postgres reference
- [`HLD.md`](./HLD.md) — engine topology + hot-path discipline
- [`Sequential_Flow.md`](./Sequential_Flow.md) — daily lifecycle
- [`Modular_Design.md`](./Modular_Design.md) — module-level design
- [`TDD.md`](./TDD.md) — per-engine implementation contracts
- [`API.md`](./API.md) — REST + WS spec
- [`Frontend_Integration.md`](./Frontend_Integration.md) — frontend contract
- [`Dev_Setup.md`](./Dev_Setup.md) — operator runbook
