# LLM Onboarding — Read This Before Touching Anything

This document is the **first thing every LLM contributor reads** at the start
of every session. It captures the working environment, the source-of-truth
rules, the GitHub workflow, and the practical PowerShell/SSH gotchas that
trip up agents on Windows hosts.

If you skip this doc you **will** waste hours debugging quoting bugs,
clobbering the wrong file, or pushing to the wrong branch. Read all of it.

---

## 1. Source of Truth (SSOT)

There are three places where code lives:

| Location | Role |
|---|---|
| **EC2** at `/home/ubuntu/premium_diff_bot/repo` | **Authoritative working tree.** All edits land here first. All tests run here. |
| **GitHub** `github.com:Yokai-2510/pcr-momentum.git` | Mirror. Updated by `git push` from EC2 (and occasionally from local). |
| **Local Windows machine** at `e:\Projects\premium_diff_bot` | Convenience mirror for the operator's IDE. Docs are usually edited locally first then synced up. Code is rarely edited here. |

**Golden rule:** when in doubt, *EC2 is right*. If local and EC2 disagree,
EC2 wins and local must be re-synced (`git pull` from local, or scp the
authoritative file down).

The only exception is `docs/`. Docs are commonly drafted locally in the IDE
and then `scp`-ed up to EC2, committed there, and pushed.

---

## 2. EC2 Connection

### 2.1 Credentials
- **Host**: `3.6.128.21`
- **User**: `ubuntu`
- **PEM key**: `e:\Projects\premium_diff_bot\nse_index_pcr_trading_pemkey.pem`
- **Repo path on EC2**: `/home/ubuntu/premium_diff_bot/repo`
- **Python venv on EC2**: `/home/ubuntu/premium_diff_bot/.venv` (already created, with `requirements*.txt` installed)

### 2.2 SSH command (Windows PowerShell)

Always use the full path to OpenSSH bundled with Windows. Do **not** rely on
PATH; some environments alias `ssh` to a broken Git-Bash version.

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" `
  -i "e:\Projects\premium_diff_bot\nse_index_pcr_trading_pemkey.pem" `
  -o StrictHostKeyChecking=no `
  -o ConnectTimeout=5 `
  ubuntu@3.6.128.21 `
  "<remote-command>"
```

Inline (single line) variant for tool calls:

```
& "C:\Windows\System32\OpenSSH\ssh.exe" -i "e:\Projects\premium_diff_bot\nse_index_pcr_trading_pemkey.pem" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@3.6.128.21 "<remote-command>"
```

### 2.3 SCP (file transfer)

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" `
  -i "e:\Projects\premium_diff_bot\nse_index_pcr_trading_pemkey.pem" `
  -o StrictHostKeyChecking=no `
  "<local-path>" `
  ubuntu@3.6.128.21:<remote-path>
```

Use `scp` for any file longer than ~30 lines or containing backticks,
dollar signs, or special quotes. PowerShell's tokenizer mangles them when
they pass through SSH heredocs.

---

## 3. PowerShell + SSH Gotchas (Read All)

These bite every LLM at least once. Internalise them now.

### 3.1 PowerShell variable interpolation
Inside a double-quoted string, PowerShell treats `$word` as a variable. A
loop like `for f in foo bar; do cat $f; done` becomes `cat ` (empty)
because `$f` was eaten by PowerShell **before** SSH ever saw the string.

**Fixes**:
- Use single quotes around the SSH payload when possible.
- Escape with backtick: `` `$f `` (PowerShell-only — does not always survive
  the round-trip).
- **Best fix**: write a small script on the remote (`/tmp/foo.sh` or
  `/tmp/foo.py`) and invoke that. No quoting nightmares.

### 3.2 Backticks get stripped
Backticks (`` ` ``) are PowerShell's line-continuation character. When you
embed a Markdown table containing inline code (`` `state/` ``) inside a
double-quoted heredoc, PowerShell silently removes the backticks.

**Symptom**: text that should read `state/, log_setup.py` arrives as
`state/, log_setup.py` with backticks gone, or worse, the heredoc gets
truncated at the first ``)``.

**Fix**: write the file locally, `scp` it up. Never inline Markdown via
heredoc through PowerShell.

### 3.3 Heredocs over ~25 lines truncate
A `cat > file <<EOF` heredoc with more than ~25 lines or any unbalanced
brackets often returns `bash: warning: here-document at line 1 delimited by
end-of-file (wanted EOF)` and the file is truncated mid-line.

**Fix**: use `scp`, or write a Python file with `python3 -c` (small) or
`/tmp/patch.py` (larger).

### 3.4 SSH hangs
Occasionally an SSH process never returns. Symptoms: tool call sits at
"running" forever.

**Recovery**:
```powershell
taskkill /F /IM ssh.exe
Start-Sleep -Seconds 2
```
Then retry. Add `-o ServerAliveInterval=10 -o ServerAliveCountMax=3` to
prevent recurrences.

### 3.5 `cd` is forbidden in tool calls
Many tools (and our internal rules) reject `cd <path> && ...`. Use:

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && <command>"
```

or set `Cwd` on the local tool that triggers SSH. Inside the SSH-passed
string, `cd` is fine.

### 3.6 Multi-line PowerShell blocks
When the IDE wraps a long SSH command across lines, the backtick at end of
each line is required for line continuation. Easier: keep the command on a
single (long) line for tool calls, and only break it across lines in the
operator-facing notes.

---

## 4. GitHub Workflow

### 4.1 Repository
- Origin: `git@github.com:Yokai-2510/pcr-momentum.git`
- Default branch: `main`
- Per-phase branches: `phase-N-short-name` (e.g. `phase-9-api-gateway`)

### 4.2 Standard cycle

```bash
# 1. Cut a phase branch from latest main
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git checkout main && git pull && git checkout -b phase-N-foo"

# 2. Make changes (locally → scp up, or directly on EC2)

# 3. Run quality gates
ssh ... "cd /home/ubuntu/premium_diff_bot/repo/backend && source /home/ubuntu/premium_diff_bot/.venv/bin/activate && ruff check . && mypy --config-file pyproject.toml . && python -m pytest -x"

# 4. Commit on EC2
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git add -A && git commit -m 'phase N: description'"

# 5. Push branch + fast-forward main
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git push -u origin phase-N-foo && git checkout main && git merge --ff-only phase-N-foo && git push origin main"
```

### 4.3 Never force-push `main`. Never amend an already-pushed commit on `main`.

---

## 5. Quality Gates (must pass before commit)

All from `/home/ubuntu/premium_diff_bot/repo/backend` with the venv active:

```bash
ruff check .
mypy --config-file pyproject.toml .
python -m pytest -x --ignore=tests/broker/live
```

Frontend (once it exists at `/home/ubuntu/premium_diff_bot/repo/frontend`):

```bash
cd /home/ubuntu/premium_diff_bot/repo/frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

If any gate fails, fix it before committing. Do not push red.

---

## 6. Project Structure (high level)

```
/home/ubuntu/premium_diff_bot/repo/
├── backend/
│   ├── alembic/               # DB migrations (Phase 2)
│   ├── brokers/upstox/        # UpstoxAPI facade (Phase 3)
│   ├── engines/
│   │   ├── init/              # 12-step bootstrapper (Phase 4)
│   │   ├── data_pipeline/     # WS ingest → tick → aggregator (Phase 5)
│   │   ├── strategy/          # premium-diff decision logic (Phase 6)
│   │   ├── order_exec/        # entry/exit/dispatcher (Phase 7)
│   │   ├── background/        # bg jobs, instrument refresh (Phase 8)
│   │   ├── scheduler/         # cron-like timers (Phase 8)
│   │   ├── health/            # probes + summary (Phase 8)
│   │   └── api_gateway/       # FastAPI + WS /stream (Phase 9)
│   ├── state/                 # Redis/Postgres clients, keys, schemas
│   ├── tests/                 # pytest suite (376 tests as of Phase 9)
│   └── pyproject.toml         # ruff + mypy + pytest config
├── frontend/                  # Next.js 14 app (Phase 10) — to be built
├── docs/                      # Design docs (this file lives here)
└── README.md
```

---

## 7. Reading the Codebase Efficiently

**Do not** cat huge files over SSH. PowerShell will truncate them.

Preferred patterns:

```bash
# List a directory
ssh ... "find /home/ubuntu/premium_diff_bot/repo/backend/engines/X -maxdepth 2 -type f -name '*.py' | sort"

# Read a small file
ssh ... "cat /path/to/small.py"

# Read line range from a big file
ssh ... "sed -n '100,200p' /path/to/big.py"

# Search across the repo
ssh ... "grep -rn 'pattern' /home/ubuntu/premium_diff_bot/repo/backend"

# Pull a file/folder to local for IDE editing
scp ... ubuntu@3.6.128.21:/path/to/file ./local/path
scp -r ... ubuntu@3.6.128.21:/path/to/dir ./local/dir
```

For multi-file inspection, tarball it first:

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && tar czf /tmp/snap.tar.gz backend/engines/api_gateway"
scp ... ubuntu@3.6.128.21:/tmp/snap.tar.gz ./snap.tar.gz
tar -xzf snap.tar.gz
```

---

## 8. Editing Code

### Small edits (<= 5 lines)
Inline `sed -i` is fine **if** the pattern has no special chars:

```bash
ssh ... "sed -i 's/old_string/new_string/' /path/to/file"
```

### Medium edits (5–50 lines)
Write a Python patch script and run it on EC2:

```bash
ssh ... "cat > /tmp/patch.py << 'PYEOF'
from pathlib import Path
p = Path('/path/to/file')
text = p.read_text()
text = text.replace('OLD_BLOCK', 'NEW_BLOCK')
p.write_text(text)
print('OK')
PYEOF
python3 /tmp/patch.py"
```

Use `'PYEOF'` (single quotes) to disable bash variable expansion inside the
heredoc. This is what survives PowerShell quoting.

### Large edits / new files
Write the file locally with the IDE editor, then `scp` it up:

```powershell
& scp.exe ... "e:\path\to\file.py" ubuntu@3.6.128.21:/home/ubuntu/premium_diff_bot/repo/...
```

Never paste >100 lines through a heredoc over PowerShell SSH. It will truncate.

---

## 9. Running Engines (manual, for testing)

Each engine is its own module with `python -m engines.<name>`:

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo/backend && source /home/ubuntu/premium_diff_bot/.venv/bin/activate && python -m engines.init"
ssh ... "... && python -m engines.data_pipeline"
ssh ... "... && python -m engines.strategy"
# etc.
```

The API gateway:

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo/backend && source /home/ubuntu/premium_diff_bot/.venv/bin/activate && uvicorn engines.api_gateway.main:app --host 0.0.0.0 --port 8000"
```

systemd units land in Phase 11; until then use foreground processes for
manual smoke tests.

---

## 10. Environment Variables

The backend expects these in `/home/ubuntu/premium_diff_bot/.env`
(symlinked / sourced into the venv activation). The LLM **never** reads or
prints secrets — only references them by name.

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres DSN |
| `REDIS_URL` | Redis URL (unix socket in prod) |
| `JWT_SECRET` | API gateway JWT signing key (>= 32 chars) |
| `CREDS_ENCRYPTION_KEY` | base64 32-byte key for AES-256-GCM cred storage |
| `APP_ENV` | `dev` | `prod` |

If a quality gate fails because of a missing env var, **stop and ask the
operator** — do not invent a placeholder value.

---

## 11. Communication Style for Agents

When working on this project:

- **Verify before claiming.** Always run `find` / `git status` / `pytest` to
  confirm state before reporting "done".
- **Cite file paths absolutely** with line ranges when referencing code.
- **Keep diffs minimal.** Surgical edits beat rewrites.
- **Update docs in the same PR as code changes.** `docs/` is not optional.
- **Commit messages** follow `phase N: short description` for phase work,
  or conventional commits (`docs: ...`, `fix: ...`, `feat: ...`).

---

## 12. Quick-Start Checklist (every session)

```
[ ] Confirm SSH works:        ssh ... "uname -a && date"
[ ] Confirm git is clean:     ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git status -s"
[ ] Confirm tests pass now:   ssh ... "cd /home/ubuntu/premium_diff_bot/repo/backend && source /home/ubuntu/premium_diff_bot/.venv/bin/activate && python -m pytest -x --ignore=tests/broker/live -q"
[ ] Read docs/Project_Plan.md for current phase
[ ] Read the phase-specific design doc (e.g. docs/frontend/*.md for Phase 10)
[ ] Create a phase branch before editing
```

If any of the first three fails, **stop and report** — the environment is
not ready and you cannot proceed safely.

---

## 13. Where to Look for What

| If you need… | Read |
|---|---|
| Project status, phase boundaries | `docs/Project_Plan.md` |
| Architecture, engine boundaries | `docs/HLD.md`, `docs/Modular_Design.md` |
| Module contracts, Python interfaces | `docs/TDD.md` |
| Strategy logic | `docs/Strategy.md` |
| System lifecycle, failsafes | `docs/Sequential_Flow.md` |
| Redis + Postgres schema | `docs/Schema.md` |
| API endpoints + payloads | `docs/API.md` |
| WebSocket protocol + view shapes | `docs/Frontend_Basics.md` |
| Frontend (Phase 10) plan | `docs/frontend/00_Frontend_Plan.md` and siblings |
| Operator runbook | `docs/Dev_Setup.md` |
| Coding conventions | `docs/LLM_Guidelines.md` |
