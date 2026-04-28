#!/usr/bin/env bash
# Finalize Phase 0 hardening and run validation checks.
set -euo pipefail

WORK="/home/ubuntu/premium_diff_bot"
REPO="$WORK/repo"

echo "===== PHASE 0 FINALIZE ====="

# ---------- UFW ----------
echo "[ufw] configuring rules..."
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# ---------- fail2ban sshd ----------
echo "[fail2ban] writing sshd jail config..."
sudo install -d -m 755 /etc/fail2ban/jail.d
sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<'EOF'
[sshd]
enabled = true
port = ssh
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
ignoreip = 127.0.0.1/8 ::1
EOF

sudo systemctl restart fail2ban

# ---------- quick checks ----------
echo "[check] ufw status"
sudo ufw status verbose

echo "[check] fail2ban status"
sudo fail2ban-client status
sudo fail2ban-client status sshd

echo "[check] service states"
for svc in redis-server postgresql nginx ufw fail2ban chrony unattended-upgrades; do
  printf "  %-22s %s\n" "$svc" "$(systemctl is-active $svc)"
done

# ---------- full audits ----------
if [ -x "$REPO/scripts/setup/audit.sh" ]; then
  echo "[audit] running audit.sh"
  bash "$REPO/scripts/setup/audit.sh"
else
  echo "[audit] audit.sh missing at $REPO/scripts/setup/audit.sh"
fi

if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
elif [ -x "$WORK/.venv/bin/python" ]; then
  PY="$WORK/.venv/bin/python"
else
  echo "[smoke] python venv missing"
  exit 1
fi

echo "[smoke] running smoke_check.py"
"$PY" "$REPO/scripts/setup/smoke_check.py"

echo "===== PHASE 0 FINALIZE COMPLETE ====="
