# Dev Setup

End-to-end setup guide. Two audiences:

- **Part A — Operator (developer)**: one-time infrastructure and code deployment. Done once when bringing up a fresh EC2.
- **Part B — Credential bootstrap design**: how the system handles missing/invalid Upstox credentials at boot. Read this before Part C so the client flow makes sense.
- **Part C — Client (end user)**: first-time runtime setup that the client does themselves through the web UI after the operator hands over the URL.

> The operator delivers a running EC2 with backend + frontend up, FastAPI reachable, and a seed admin login. The client never touches the EC2.

---

## Part A — Operator Setup

### A.1 AWS account prerequisites

- AWS account with billing alarm configured (the bot is small; the alarm is to catch mistakes).
- Create an IAM user (not root) with `AmazonEC2FullAccess` for the setup. Use this user's access key for the AWS CLI if you script anything; otherwise console is fine.
- Pick region **`ap-south-1` (Mumbai)** to minimise latency to the broker.

### A.2 EC2 instance

| Setting | Value | Why |
|---|---|---|
| AMI | Ubuntu Server 22.04 LTS (x86-64) | Stable, long support, matches our Python target |
| Instance type | `t3.medium` (2 vCPU / 4 GB) for staging; `t3.large` or `c6i.large` for live | Strategy + WS + Postgres + Redis fit comfortably |
| Storage | 30 GB `gp3` root | Logs + Postgres durability |
| Network | Default VPC, public subnet, **auto-assign public IP = yes** | Required for inbound HTTPS |
| Security group | See A.3 | |
| Key pair | Create new `.pem`, download, store securely on your dev box | Only auth method |
| Elastic IP | Allocate one, associate to the instance | So DNS doesn't break on stop/start |

### A.3 Security group rules

Inbound:

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22  | TCP | **your home IP / VPN CIDR only** | SSH |
| 80  | TCP | `0.0.0.0/0` | HTTP → redirect to HTTPS |
| 443 | TCP | `0.0.0.0/0` | HTTPS (frontend + REST + WS) |

Outbound: allow all (default).

**Never** open 5432 (Postgres), 6379 (Redis), 8000 (FastAPI), or 3000 (Next.js) to the internet. Those stay loopback-only behind Nginx.

### A.4 DNS + TLS

- Point an A-record (e.g. `bot.example.com`) at the Elastic IP at your registrar.
- Wait for propagation (usually a few minutes).
- TLS is provisioned in step A.10 via certbot.

### A.5 First SSH in + base hardening

```bash
chmod 400 ./bot.pem                              # on your dev machine
ssh -i ./bot.pem ubuntu@<elastic-ip>

# Once in:
sudo apt update && sudo apt -y upgrade
sudo timedatectl set-timezone Asia/Kolkata
sudo apt -y install ufw fail2ban chrony unattended-upgrades

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

sudo systemctl enable --now chrony            # time sync, matters for option timestamps
sudo systemctl enable --now fail2ban          # SSH brute-force defence
```

### A.6 Create the app user

```bash
sudo useradd -m -s /bin/bash trader
sudo usermod -aG sudo trader                  # remove later if you want least-privilege
sudo mkdir -p /home/trader/.ssh
sudo cp ~/.ssh/authorized_keys /home/trader/.ssh/
sudo chown -R trader:trader /home/trader/.ssh
sudo chmod 700 /home/trader/.ssh
sudo chmod 600 /home/trader/.ssh/authorized_keys
```

All app processes run as `trader`. Log out and re-SSH as `trader`:

```bash
ssh -i ./bot.pem trader@<elastic-ip>
```

### A.7 System dependencies

```bash
sudo apt -y install \
    build-essential pkg-config curl wget git \
    python3.11 python3.11-venv python3.11-dev \
    redis-server postgresql postgresql-contrib \
    nginx certbot python3-certbot-nginx \
    htop tmux jq
```

If `python3.11` is not in the default repo for 22.04 (older mirrors), add the deadsnakes PPA:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt -y install python3.11 python3.11-venv python3.11-dev
```

### A.8 Redis configuration

Edit `/etc/redis/redis.conf`:

```conf
# Use a Unix socket (faster + safer than TCP)
unixsocket /var/run/redis/redis.sock
unixsocketperm 770
port 0                                  # disable TCP entirely
bind 127.0.0.1 -::1                     # belt and braces; only matters if port re-enabled
maxmemory 1gb
maxmemory-policy allkeys-lru
appendonly no                           # we FLUSHDB daily; AOF is wasted IO
save ""                                 # no RDB snapshots either
```

```bash
sudo usermod -aG redis trader
sudo systemctl restart redis-server
sudo systemctl enable redis-server
redis-cli -s /var/run/redis/redis.sock ping       # → PONG
```

### A.9 PostgreSQL setup

```bash
sudo -u postgres psql <<SQL
CREATE USER trader WITH PASSWORD '<strong-random>';
CREATE DATABASE premium_diff_bot OWNER trader;
GRANT ALL PRIVILEGES ON DATABASE premium_diff_bot TO trader;
SQL

# Local socket auth — edit pg_hba.conf to set 'peer' for local trader user, no password needed.
sudo sed -i '/^local.*all.*all.*peer$/c\local   all             trader                                  peer\nlocal   all             all                                     peer' /etc/postgresql/14/main/pg_hba.conf
sudo systemctl restart postgresql
psql -d premium_diff_bot -c "SELECT 1;"           # should print 1
```

Migrations (run from the repo after A.11):

```bash
cd /home/trader/premium_diff_bot
source .venv/bin/activate
alembic upgrade head
```

### A.10 Nginx + TLS

Drop a single config at `/etc/nginx/sites-available/bot`:

```nginx
server {
    listen 80;
    server_name bot.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name bot.example.com;

    # certbot fills these in
    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    # Frontend (Next.js) — production build served by node, not next dev
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    # REST
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    # WebSocket
    location /stream {
        proxy_pass http://127.0.0.1:8000/stream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    # Webhook (Upstox calls this directly; no JWT)
    location /api/auth/upstox-webhook {
        proxy_pass http://127.0.0.1:8000/auth/upstox-webhook;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/bot /etc/nginx/sites-enabled/bot
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo certbot --nginx -d bot.example.com --non-interactive --agree-tos -m you@example.com
sudo systemctl reload nginx
```

Certbot auto-renews via systemd timer; verify with `sudo systemctl list-timers | grep certbot`.

### A.11 Repo + Python env

```bash
cd /home/trader
git clone https://github.com/<you>/premium_diff_bot.git
cd premium_diff_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r backend/requirements.txt
```

### A.12 Repo on GitHub — deploy key

So the EC2 can `git pull` private repos without your personal key:

```bash
ssh-keygen -t ed25519 -C "ec2-deploy" -f ~/.ssh/deploy_ed25519 -N ""
cat ~/.ssh/deploy_ed25519.pub                   # copy this
```

Add the printed key as a **read-only deploy key** under the GitHub repo's *Settings → Deploy keys*. Then:

```bash
cat >> ~/.ssh/config <<EOF
Host github.com
  IdentityFile ~/.ssh/deploy_ed25519
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
git remote set-url origin git@github.com:<you>/premium_diff_bot.git
git pull            # should succeed
```

### A.13 `.env` file

Single source of secrets at `/home/trader/premium_diff_bot/.env`. **Never** committed. Permissions `600`, owner `trader`.

```bash
# === Database ===
DATABASE_URL=postgresql+asyncpg://trader@/premium_diff_bot?host=/var/run/postgresql
REDIS_SOCKET=/var/run/redis/redis.sock

# === Encryption-at-rest ===
# 32-byte base64 key. Generate once: python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
CREDS_ENCRYPTION_KEY=<paste-base64-here>

# === JWT ===
JWT_SECRET=<32+ random chars; rotated only by manual ops>
JWT_EXPIRY_MINUTES=720

# === Seed admin (used ONLY on first boot if users table is empty) ===
SEED_ADMIN_USERNAME=admin
SEED_ADMIN_PASSWORD=<strong; user changes on first login>

# === FastAPI ===
API_HOST=127.0.0.1
API_PORT=8000
CORS_ORIGINS=https://bot.example.com

# === Frontend ===
NEXT_PUBLIC_API_BASE=https://bot.example.com/api
NEXT_PUBLIC_WS_URL=wss://bot.example.com/stream

# === Logging ===
LOG_LEVEL=INFO
```

Generate the encryption key:

```bash
python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

> **Critical**: `CREDS_ENCRYPTION_KEY` and `JWT_SECRET` are the only true secrets on disk. If either leaks or rotates, all stored Upstox credentials become unreadable and the user must re-enter them.

### A.14 Frontend build

```bash
cd /home/trader/premium_diff_bot/frontend
sudo apt -y install nodejs npm                  # or use nvm for a newer node
npm ci
npm run build
```

The frontend is then started by systemd (next entry).

### A.15 systemd units

Create `/etc/systemd/system/trading-stack.target`:

```ini
[Unit]
Description=Premium-Diff Trading Stack
Wants=trading-data-pipeline.service trading-strategy.service trading-order-exec.service trading-background.service trading-scheduler.service trading-health.service trading-api.service trading-frontend.service
After=trading-init.service
```

Create one unit per engine; example for `trading-api`:

```ini
# /etc/systemd/system/trading-api.service
[Unit]
Description=Trading FastAPI Gateway
After=network.target redis-server.service postgresql.service
PartOf=trading-stack.target

[Service]
User=trader
Group=trader
WorkingDirectory=/home/trader/premium_diff_bot
EnvironmentFile=/home/trader/premium_diff_bot/.env
ExecStart=/home/trader/premium_diff_bot/.venv/bin/uvicorn engines.api_gateway.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=2

[Install]
WantedBy=trading-stack.target
```

Repeat for `trading-init` (oneshot, `OnSuccess=trading-stack.target`), `trading-data-pipeline`, `trading-strategy`, `trading-order-exec`, `trading-background`, `trading-scheduler`, `trading-health`, `trading-frontend` (runs `npm run start`).

Daily timers (run as `root`):

```ini
# /etc/systemd/system/trading-start.timer
[Unit]
Description=Daily 08:00 IST start

[Timer]
OnCalendar=Mon..Fri *-*-* 08:00:00 Asia/Kolkata
Unit=trading-init.service

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/trading-stop.timer
[Unit]
Description=Daily 15:46 IST stop

[Timer]
OnCalendar=Mon..Fri *-*-* 15:46:00 Asia/Kolkata
Unit=trading-stop.service

[Install]
WantedBy=timers.target
```

Reload + enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-api.service trading-frontend.service
sudo systemctl enable trading-start.timer trading-stop.timer
sudo systemctl start trading-api.service trading-frontend.service
```

`trading-api` and `trading-frontend` stay up 24/7. The cyclic engines (init, data-pipeline, strategy, etc.) are brought up by the daily 08:00 timer and torn down at 15:46.

### A.16 Smoke test

```bash
curl -s https://bot.example.com/api/health | jq .
# expect: { "summary": "DEGRADED", "auth": "missing", ... }   ← because no Upstox creds yet
```

Visit `https://bot.example.com` in any browser; you should see the login screen. Hand over the URL + seed admin creds to the client. Operator setup is done.

---

## Part B — Credential Bootstrap Design

The operator delivers a system with **no Upstox credentials**. The client provides them via the web UI on first login. Until they're valid, the system is intentionally idle but the API + frontend are alive.

### B.1 New system flag

`system:flags:trading_disabled_reason` — STRING, one of:

| Value | Meaning |
|---|---|
| `none` | Trading is allowed |
| `awaiting_credentials` | Init ran, but no Upstox creds present in `user_credentials` |
| `auth_invalid` | Creds present but `/v2/user/profile` probe failed |
| `holiday` | Today is a holiday or non-standard session |
| `manual_kill` | User toggled global halt |
| `circuit_tripped` | Daily-loss circuit fired |

`system:flags:trading_active` is set to `"false"` whenever `trading_disabled_reason != "none"`. The strategy and order-exec engines treat both flags identically — they idle.

### B.2 Init's branching logic at STEP 6

```
STEP 6: Bootstrap Upstox auth
  a) SELECT encrypted_value FROM user_credentials WHERE broker='upstox'
  b) If row missing OR decrypt fails:
       SET system:health:auth = missing
       SET system:flags:trading_disabled_reason = awaiting_credentials
       SET system:flags:trading_active = false
       SKIP STEPs 7–11 (no broker calls possible)
       exit 0  → trading-stack.target activates with engines IDLE
  c) Decrypt → cache to user:credentials:upstox (Redis JSON)
  d) Probe /v2/user/profile with cached access_token
  e) If profile 200:
       SET system:health:auth = valid
       continue to STEP 7
  f) If profile 401 or token expired:
       attempt v3 token refresh in background (non-blocking)
       SET system:health:auth = invalid
       SET system:flags:trading_disabled_reason = auth_invalid
       SET system:flags:trading_active = false
       SKIP STEPs 7–11
       exit 0  → stack idle until user clicks "Request Token" in UI
```

This replaces the previous fail-stop behaviour for credential errors. `exit 1` is reserved only for **infra failures** (Redis/Postgres down) — never for missing user input.

### B.3 Recovery without restart

Once the client submits valid creds via `POST /credentials/upstox`, the API gateway:

1. Encrypts and writes them to Postgres + Redis.
2. Probes `/v2/user/profile` synchronously.
3. On success: sets `system:health:auth = valid`, clears `trading_disabled_reason`, sets `trading_active = true`, publishes `system:pub:system_event {auth_recovered}`.
4. Strategy and Data-Pipeline engines wake on the pub/sub and run their normal "engine start" sequence (subscribe broker WS, take pre-open snapshot if before 09:14:50, etc.).

No engine restart, no Init re-run. The next daily 08:00 cycle picks up the now-valid creds normally.

### B.4 Why not store creds in `.env`

- `.env` requires SSH + sudo to edit; we want clients to manage their own keys.
- `.env` files leak via shell history, backups, log scrapers.
- Encrypted-in-Postgres + frontend rotation is the standard pattern; `CREDS_ENCRYPTION_KEY` is the only thing on disk and it's never displayed.

---

## Part C — Client Setup (web UI)

### C.1 First login

1. Open `https://bot.example.com` in any modern browser.
2. Login with the seed admin credentials provided by the operator.
3. The dashboard loads in **degraded mode**: a yellow banner reads *"Awaiting Upstox credentials — trading disabled until configured."*
4. Go to **Settings → Account** and change the password immediately.

### C.2 Upstox application registration (done once on Upstox's side)

1. Sign in to <https://account.upstox.com/developer/apps>.
2. Click *Create new app*.
3. Fill in:
   - **App name**: anything (e.g. `premium-diff-bot`)
   - **Redirect URI**: `https://bot.example.com/api/auth/upstox-webhook`
   - **Postback URL**: `https://bot.example.com/api/auth/upstox-webhook`
   - Permissions: read order, read position, read holdings, place order, modify order, cancel order
4. Save. Copy the **API Key** and **API Secret** — these are shown once.

### C.3 TOTP secret

Upstox v3 auto-login needs the **TOTP seed** (the long base32 string), not the rotating 6-digit code.

1. In the Upstox web app: *Profile → 2FA → Authenticator app*.
2. When the QR code is shown, click *Can't scan? Show key* — that's the seed (e.g. `JBSWY3DPEHPK3PXP...`).
3. Save it; you'll paste it into the bot UI in C.4.

> If 2FA is already enabled with an authenticator and the seed is lost, disable 2FA and re-enable to get a fresh seed.

### C.4 Enter Upstox credentials in the bot

In the bot UI: **Settings → Broker → Upstox**.

| Field | Source | Required |
|---|---|---|
| API Key | C.2 | Yes |
| API Secret | C.2 | Yes |
| Redirect URI | Same as registered in C.2 | Yes |
| TOTP Secret | C.3 | Yes |
| Mobile Number | Upstox-registered mobile (10 digits) | Yes |
| PIN | Upstox 6-digit login PIN | Yes |
| Analytics Token | Optional, from Upstox developer console | No |
| Sandbox Token | Optional, for paper-mode testing | No |

Click **Validate & Save**.

The backend:

1. Encrypts and stores the bundle.
2. Probes `/v2/user/profile` with the analytics token (instant validation without needing the access-token flow).
3. If valid → green checkmark, status flips to *Authenticated*.
4. If invalid → red banner with the broker error code; fix the bad field and re-save.

### C.5 First access-token request

Click **Request Access Token**. The backend calls `POST /commands/upstox_token_request`, which sends an approval prompt to your Upstox mobile app and WhatsApp.

1. Approve in the Upstox app within 10 minutes.
2. Upstox calls our webhook `/api/auth/upstox-webhook` with the access token.
3. UI status changes to *Token live until <expiry>*. The dashboard banner clears.

The token is auto-refreshed daily at 03:30 IST by the Background engine. You only do C.5 once, unless 2FA / API credentials change.

### C.6 Trading configuration

In **Settings → Trading**:

- **Mode**: `paper` (recommended for first 1–2 weeks) or `live`.
- **Indexes**: enable `nifty50` and/or `banknifty`.
- **Risk**: set per-trade max loss, daily loss circuit, max trades/day, max reversals/day.
- **Auto-continue**: turn ON to let the daily 08:00 timer run automatically. OFF means you must manually click *Start Today* every morning.

Click **Save**. The Init engine picks these up at the next 08:00 cycle.

### C.7 Daily expectations

- 08:00 IST: Init runs. If everything is healthy, the stack comes up.
- 09:14:50 IST: Pre-open snapshots captured.
- 09:15:00 IST: Trading begins.
- 15:15 IST: EOD square-off; all positions closed.
- 15:30 IST: Market close; report email sent (if configured).
- 15:46 IST: Stack stops; only Postgres, Redis, Nginx, FastAPI stay up.

The UI is reachable 24/7. Outside trading hours, the dashboard shows "Market closed — last session summary" and historical screens (positions, PnL history, reports) work normally.

### C.8 Things the client never has to do

- Never SSH into the EC2.
- Never edit any file on the server.
- Never restart any service manually.
- Never enter credentials in any file — only in the UI.
- Never wait for an engine restart to apply config changes; all are hot-reloadable.

If something is broken (banner stays red, UI says "Auth invalid"), the recovery path is always the same: **re-validate creds in Settings, then click Request Access Token**.

---

## Appendix — Operator Cheat Sheet

```bash
# Tail one engine
journalctl -u trading-strategy.service -f

# Tail everything
journalctl -u 'trading-*' -f

# Restart one engine without touching others
sudo systemctl restart trading-strategy.service

# Manual init (outside the daily timer)
sudo systemctl start trading-init.service

# Manual stop everything
sudo systemctl stop trading-stack.target

# Pull and redeploy
cd /home/trader/premium_diff_bot
git pull
source .venv/bin/activate && pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart trading-api.service trading-frontend.service
# Cyclic engines pick up the new code on the next 08:00 timer; or restart trading-stack.target manually.

# Rotate JWT secret (forces all logins out)
# 1. Edit .env: change JWT_SECRET
# 2. sudo systemctl restart trading-api.service
```
