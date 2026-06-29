#!/usr/bin/env bash
# Deploy the pi-turret monitoring config to this box (run ON the Pi after `git push pi main`).
#   ssh pi
#   ~/pi-turret/monitoring/deploy.sh
# Copies the version-controlled config into /etc, validates, and reloads Alloy. Does NOT touch
# /etc/alloy/secrets.env (the token/endpoints live there, chmod 600, never in git) and does NOT
# enable/start turret.service (manual-start by design).
set -euo pipefail
REPO="${1:-$HOME/pi-turret}"
cd "$REPO/monitoring"

echo "==> installing config files into /etc"
sudo install -m 0644 config.alloy                /etc/alloy/config.alloy
sudo install -m 0644 default-alloy               /etc/default/alloy
sudo mkdir -p /etc/systemd/system/alloy.service.d
sudo install -m 0644 systemd/alloy-override.conf /etc/systemd/system/alloy.service.d/override.conf
sudo install -m 0644 systemd/turret.service      /etc/systemd/system/turret.service

if [ ! -f /etc/alloy/secrets.env ]; then
  echo "!! /etc/alloy/secrets.env missing — copy secrets.env.example there, chmod 600, and fill it."
  exit 1
fi

echo "==> validating config.alloy (secrets sourced)"
sudo bash -c 'set -a; . /etc/alloy/secrets.env; set +a; alloy validate /etc/alloy/config.alloy'

echo "==> reloading systemd + Alloy"
sudo systemctl daemon-reload
sudo systemctl reload-or-restart alloy

echo "==> done. Alloy: $(systemctl is-active alloy) / $(systemctl is-enabled alloy)"
echo "    turret.service: $(systemctl is-enabled turret 2>/dev/null || echo not-installed) (start manually: sudo systemctl start turret)"
