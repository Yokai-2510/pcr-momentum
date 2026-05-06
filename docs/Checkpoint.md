# Session Checkpoint — Phase 11 deployed; backend production-ready

Last updated: 2026-05-06.

If this chat is discontinued, read this file first, then [docs/Project_Plan.md](Project_Plan.md),
then the relevant sibling docs.

---

## 1. Where we are

- **Phases 0–9** complete and on `main`.
- **Phase 11 deployed** to EC2 `3.6.128.21` (Elastic IP, `ap-south-1`). All systemd units,
  reverse proxy, TLS, backups, and log rotation are in production.
- **Phase 10a (frontend UI)** scaffolded locally; will be hosted on Vercel and ships as a
  separate effort. The backend deliberately does not host any frontend.
- **Phase 12 (paper-trade)** pending the next trading day.

## 2. Public API endpoints

| What | URL |
|---|---|
| API base | `https://3-6-128-21.sslip.io/api/` |
| Health | `https://3-6-128-21.sslip.io/api/health` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream` |
| Upstox webhook | `https://3-6-128-21.sslip.io/api/auth/upstox-webhook` |

Lets Encrypt cert auto-renews via `certbot.timer`. CORS allows `http://localhost:3000` for
dev plus any `https://*.vercel.app` origin (regex). JWT is the actual auth.

## 3. Daily lifecycle on EC2

| Time (IST) | Event | Trigger |
|---|---|---|
| 08:00 | `pcr-start.timer` fires `pcr-init.service` | systemd timer |
| 08:00–08:01 | Init runs the 12-step precheck (FLUSHDB, hydrate from PG, broker probe, basket build) | `pcr-init.service` |
| 08:01 | `pcr-stack.target` activates — health, data-pipeline, background, strategy, order-exec, scheduler all start | `Wants=pcr-stack.target` on init |
| 09:14:00 | Data Pipeline opens broker market WS, subscribes 54 tokens | Scheduler control event |
| 09:14:50 | Strategy captures pre-open snapshot per index | Scheduler control event |
| 09:15:00 | Settle window begins; live decision loop from 09:15:10 | Scheduler |
| 15:15 | EOD square-off | Scheduler |
| 15:30 | Market close, broker WS closed | Scheduler |
| 15:45 | `pcr-stop.timer` fires `pcr-stop.service` → `/usr/local/bin/pcr-shutdown.sh` | systemd timer |
| 15:46 | shutdown.sh `systemctl stop`s the stack target and init.service after 60 s drain | shutdown.sh |
| 15:46 → 08:00 next day | OFF — only Postgres, Redis, Nginx, `pcr-api-gateway` remain up | — |

`pcr-api-gateway` is persistent: REST and WS are reachable 24/7 so the operator can review
history, edit configs, and submit credentials overnight. Only the cyclic engines cycle.

## 4. systemd at a glance

```
pcr-stack.target            Requires=pcr-init.service    After=pcr-init.service
├─ pcr-init.service         Type=oneshot, RemainAfterExit=yes
├─ pcr-health.service       After=pcr-init.service, PartOf=pcr-stack.target
├─ pcr-data-pipeline.service
├─ pcr-background.service
├─ pcr-strategy.service
├─ pcr-order-exec.service
└─ pcr-scheduler.service

pcr-api-gateway.service     persistent (not in stack.target)
pcr-start.timer             Mon-Fri 08:00 IST → pcr-init.service
pcr-stop.timer              Mon-Fri 15:45 IST → pcr-stop.service
```

Canonical files live in `scripts/systemd/` and are deployed via `scripts/systemd/install.sh`
(idempotent, safe after a `git pull`).

## 5. Operational cheat-sheet on EC2

```bash
# Status
systemctl list-timers pcr-*
systemctl status pcr-stack.target pcr-api-gateway.service
journalctl -u "pcr-*" -f

# Manual lifecycle (testing only)
sudo systemctl start pcr-init.service        # → cascades to stack.target
sudo systemctl start pcr-stop.service        # → graceful drain + stop

# Pull latest code; restart api-gateway only (cyclic engines pick up at next 08:00)
cd /home/ubuntu/premium_diff_bot/repo && git pull
sudo systemctl restart pcr-api-gateway.service

# Backups
ls /var/backups/pg/        # nightly dumps, 7-day retention
sudo -u postgres pg_dump -Fc premium_diff_bot -f /tmp/manual.dump   # one-shot
```

## 6. Things still pending

1. **Phase 10a UI polish** — frontend on Vercel; backend already CORS-permits any `*.vercel.app` origin.
2. **Phase 10b** — analytics endpoints + new tables; deferred per user.
3. **Phase 12** — 5 paper-trade days, then go-live.
4. Strategy mid-day cold-start (manual restart after 09:14:50) currently disables the indexes
   because there is no pre-open snapshot to validate against. This is acceptable for normal
   8-AM-timer operation; revisit if mid-day systemd restart becomes a real ops scenario.

## 7. How to resume if this chat is gone

1. Read this file top-to-bottom.
2. Read [Project_Plan.md](Project_Plan.md) §Recommended order of operations.
3. `git log --oneline -20` in the repo.
4. `curl -s https://3-6-128-21.sslip.io/api/health | jq .` — should return 200.
5. `systemctl list-timers pcr-*` — confirm both timers armed.
6. `journalctl -u pcr-init.service --since "08:00 IST" -n 50` for the latest precheck output.
