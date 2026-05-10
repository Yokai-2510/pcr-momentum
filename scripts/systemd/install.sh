#!/bin/bash
# PCR Momentum — install systemd units, helper scripts, nginx, cron, logrotate.
# Idempotent: safe to re-run after a "git pull".
set -e

REPO=/home/ubuntu/premium_diff_bot/repo
SYSTEMD_DIR=$REPO/scripts/systemd

echo "==> installing systemd units"
# pcr-strategy@.service is a template unit. Per-strategy instances are
# activated by pcr-stack.target's `Wants=pcr-strategy@<sid>.service` lines.
# Adding a strategy: append a Wants line in pcr-stack.target, re-run this
# script, then `systemctl enable --now pcr-strategy@<sid>.service`.
# `pcr-strategy.service` (the legacy singleton) must NOT exist on disk.
sudo rm -f /etc/systemd/system/pcr-strategy.service
sudo cp $SYSTEMD_DIR/pcr-*.service $SYSTEMD_DIR/pcr-*.target $SYSTEMD_DIR/pcr-*.timer /etc/systemd/system/
sudo cp "$SYSTEMD_DIR/pcr-strategy@.service" /etc/systemd/system/pcr-strategy@.service
sudo chmod 644 /etc/systemd/system/pcr-*.service /etc/systemd/system/pcr-*.target /etc/systemd/system/pcr-*.timer 2>/dev/null || true

echo "==> installing /usr/local/bin/pcr-shutdown.sh"
sudo install -m 0755 -o root -g root $REPO/scripts/pcr-shutdown.sh /usr/local/bin/pcr-shutdown.sh

echo "==> installing nginx vhost"
sudo install -m 0644 $REPO/scripts/nginx/pcr.conf /etc/nginx/sites-available/pcr
sudo ln -sf /etc/nginx/sites-available/pcr /etc/nginx/sites-enabled/pcr
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo "==> installing cron + logrotate"
sudo install -m 0644 $REPO/scripts/cron/pcr-pg-backup /etc/cron.d/pcr-pg-backup
sudo install -m 0644 $REPO/scripts/logrotate/pcr /etc/logrotate.d/pcr

echo "==> daemon-reload + enable timers and api-gateway"
sudo systemctl daemon-reload
sudo systemctl enable pcr-api-gateway.service pcr-start.timer pcr-stop.timer
sudo systemctl start pcr-api-gateway.service pcr-start.timer pcr-stop.timer

echo "==> done. Inspect via:"
echo "    systemctl list-timers pcr-*"
echo "    systemctl status pcr-api-gateway.service"
echo "    sudo certbot --nginx -d <hostname>   # one-shot, then certbot.timer auto-renews"
