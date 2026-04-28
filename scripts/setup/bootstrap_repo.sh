#!/usr/bin/env bash
# Bootstrap the EC2 working tree by cloning the GitHub repo, migrating all
# previously-created assets (docs/, scripts/, backend/requirements*.txt) into
# it, writing a strict .gitignore, and pushing the initial commit.
#
# Idempotent: safe to re-run. If the repo is already cloned, it just syncs
# new local files in.

set -euo pipefail

WORK="/home/ubuntu/premium_diff_bot"           # current scratch tree
REPO_DIR="$WORK/repo"                          # final cloned repo
REMOTE="git@github.com:Yokai-2510/pcr-momentum.git"

# ---------- 1. Clone (or refresh) ----------
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REMOTE" "$REPO_DIR"
else
  git -C "$REPO_DIR" remote set-url origin "$REMOTE"
  git -C "$REPO_DIR" fetch --all --prune || true
fi

cd "$REPO_DIR"

# ---------- 2. Identity ----------
git config user.name  "ec2-trading-bot"
git config user.email "ec2-trading-bot@pcr-momentum.local"

# ---------- 3. Migrate scratch files into repo ----------
# docs/ and scripts/ — copy entire trees if they exist in $WORK
for d in docs scripts; do
  if [ -d "$WORK/$d" ]; then
    mkdir -p "$REPO_DIR/$d"
    rsync -a --exclude='.git' "$WORK/$d/" "$REPO_DIR/$d/"
  fi
done

# backend/requirements*.txt — keep them at backend/
if [ -d "$WORK/backend" ]; then
  mkdir -p "$REPO_DIR/backend"
  rsync -a --exclude='.git' --exclude='__pycache__' \
    "$WORK/backend/" "$REPO_DIR/backend/"
fi

# ---------- 4. .gitignore ----------
cat > "$REPO_DIR/.gitignore" <<'EOF'
# ----- Secrets & local config -----
.env
.env.*
!.env.example
.db_secret
credentials.json
*.pem
*.key
nse_index_pcr_trading_pemkey.pem

# ----- Python -----
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
*.egg
.eggs/
build/
dist/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
.tox/
.venv/
venv/
env/
ENV/
.python-version

# ----- Node / Frontend -----
node_modules/
.next/
out/
.nuxt/
.svelte-kit/
.turbo/
.parcel-cache/
.pnpm-store/
.npm/
.yarn/
*.tsbuildinfo
.eslintcache

# ----- Editors / OS -----
.idea/
.vscode/
*.swp
*.swo
.DS_Store
Thumbs.db

# ----- Logs / runtime data -----
logs/
*.log
*.log.*
*.pid
runtime/
tmp/
temp/

# ----- Bot-specific local artefacts -----
# Local broker data dumps, replay tapes, capture files
captures/
replays/
*.pcap

# Alembic auto-generated cache (keep migration scripts!)
alembic/versions/__pycache__/

# Playwright local browsers (we install via venv)
.cache/ms-playwright/
EOF

# ---------- 5. .env.example ----------
cat > "$REPO_DIR/.env.example" <<'EOF'
# Copy to .env (chmod 600) and fill in real values.
# .env is git-ignored. Never commit real secrets.

APP_ENV=production

# ---- Datastores ----
DATABASE_URL=postgresql+asyncpg://trader:CHANGE_ME@127.0.0.1:5432/premium_diff_bot
REDIS_URL=redis+unix:///var/run/redis/redis.sock?db=0

# ---- Server-side secrets (generated once during bootstrap) ----
CREDS_ENCRYPTION_KEY=<base64-urlsafe 32 random bytes>
JWT_SECRET=<urlsafe 48 random bytes>
SEED_ADMIN_PASSWORD=<random>

# ---- Upstox creds (sourced from credentials.json by bootstrap script) ----
UPSTOX_API_KEY=
UPSTOX_API_SECRET=
UPSTOX_REDIRECT_URI=
UPSTOX_TOTP_KEY=
UPSTOX_MOBILE_NO=
UPSTOX_PIN=
UPSTOX_ANALYTICS_TOKEN=
EOF

# ---------- 6. Minimal README so the initial commit isn't bare ----------
if [ ! -f "$REPO_DIR/README.md" ] || ! grep -q "premium-diff" "$REPO_DIR/README.md" 2>/dev/null; then
  cat > "$REPO_DIR/README.md" <<'EOF'
# pcr-momentum

Premium-Diff Multi-Index Trading Bot — Python backend (FastAPI + asyncpg +
Redis), Next.js frontend, Upstox broker SDK.

## Documentation

All design docs live under [`docs/`](./docs):

- `HLD.md` — high-level design
- `TDD.md` — technical design / module contracts
- `Modular_Design.md` — per-module responsibility & interface
- `Strategy.md` — premium-diff momentum strategy spec
- `Sequential_Flow.md` — system lifecycle & failsafes
- `Schema.md` — Redis + Postgres schema
- `API.md` — FastAPI gateway contract
- `Frontend_Basics.md` — push-only WS + view contract
- `Dev_Setup.md` — operator + dev-machine setup runbook
- `LLM_Guidelines.md` — coding standards for contributors
- `Project_Plan.md` — phased delivery plan

## Status

Phase 0 — infrastructure bootstrap, in progress.
EOF
fi

# ---------- 7. Stage / commit / push ----------
git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -m "Bootstrap: docs, requirements, setup scripts, gitignore"
fi
# Determine current branch name; default to main if empty repo
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo main)
git push -u origin "$BRANCH"

echo "----- DONE -----"
git -C "$REPO_DIR" log --oneline -n 5
echo "Tree summary:"
git -C "$REPO_DIR" ls-files | head -40
echo "..."
echo "Total tracked files: $(git -C "$REPO_DIR" ls-files | wc -l)"
