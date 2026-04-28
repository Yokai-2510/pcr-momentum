#!/usr/bin/env bash
# Generate (idempotent) an ed25519 deploy key for the pcr-momentum repo
# and configure SSH so `git@github.com:Yokai-2510/pcr-momentum.git` uses it.
set -euo pipefail

KEY="$HOME/.ssh/github_deploy"
SSH_CONFIG="$HOME/.ssh/config"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -C "ec2-pcr-momentum-deploy" -N "" -f "$KEY" >/dev/null
fi
chmod 600 "$KEY"
chmod 644 "${KEY}.pub"

if ! grep -q "Host github.com" "$SSH_CONFIG" 2>/dev/null; then
  cat >> "$SSH_CONFIG" <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
  chmod 600 "$SSH_CONFIG"
fi

echo "----- PUBLIC KEY (paste into GitHub > Deploy Keys, tick 'Allow write access') -----"
cat "${KEY}.pub"
echo "----- FINGERPRINT -----"
ssh-keygen -lf "${KEY}.pub"
