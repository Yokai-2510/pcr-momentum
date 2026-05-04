#!/bin/bash
set -e
DIR=/home/ubuntu/premium_diff_bot/repo/scripts/systemd
sudo cp ${DIR}/*.service ${DIR}/*.timer /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/pcr-*.service /etc/systemd/system/pcr-*.timer 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable pcr-start.timer pcr-stop.timer pcr-api-gateway.service
sudo systemctl start pcr-api-gateway.service || true
echo "Systemd units installed. Timers enabled for auto-start."
