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
sudo install -m 0644 systemd/turret.service        /etc/systemd/system/turret.service
sudo install -m 0644 systemd/turret-remote.service /etc/systemd/system/turret-remote.service
sudo install -m 0644 systemd/pi-turret-ir.service  /etc/systemd/system/pi-turret-ir.service
sudo mkdir -p /etc/rc_keymaps
sudo install -m 0644 rc_keymaps/pi_turret.toml     /etc/rc_keymaps/pi_turret.toml
chmod +x ir-load-keytable.sh

if ! sudo test -f /etc/alloy/secrets.env; then
  echo "!! /etc/alloy/secrets.env missing — copy secrets.env.example there, chmod 600, and fill it."
  exit 1
fi  # NB: /etc/alloy is root-only, so test as root — a bare [ -f ] runs as the deploy user and false-negatives.

echo "==> validating config.alloy (secrets sourced)"
sudo bash -c 'set -a; . /etc/alloy/secrets.env; set +a; alloy validate /etc/alloy/config.alloy'

echo "==> reloading systemd + Alloy"
sudo systemctl daemon-reload
sudo systemctl reload-or-restart alloy

echo "==> IR remote: keytable loader + supervisor"
# Deps the root-run supervisor + keytable need (system-wide so root can import them):
#   ir-keytable = NEC keytable loader; python3-evdev = read /dev/input; python3-yaml = read config*.yaml.
# (turret.service runs as jayson and may use jayson's pip yaml, but turret-remote runs as ROOT.)
for pkg in ir-keytable python3-evdev python3-yaml; do
  dpkg -s "$pkg" >/dev/null 2>&1 || { echo "   installing $pkg"; sudo apt-get install -y "$pkg"; }
done
# Keytable oneshot: enable for boot; try now but tolerate a missing device (the
# dtoverlay=gpio-ir,gpio_pin=25 line + reboot may not be in place on first deploy).
sudo systemctl enable pi-turret-ir.service >/dev/null 2>&1 || true
sudo systemctl restart pi-turret-ir.service 2>/dev/null \
  || echo "   !! keytable not loaded — add 'dtoverlay=gpio-ir,gpio_pin=25' to /boot/config.txt + reboot, then re-run"
# Supervisor: always-on, enabled on boot. Self-exits (inactive) if remote.enabled is False;
# set 'remote.enabled: true' in config.local.yaml to activate.
sudo systemctl enable turret-remote.service >/dev/null 2>&1 || true
sudo systemctl restart turret-remote.service 2>/dev/null || true

te=$(systemctl is-enabled turret 2>/dev/null || true)
echo "==> done. Alloy: $(systemctl is-active alloy) / $(systemctl is-enabled alloy)"
echo "    turret.service:        ${te:-not-installed} (start manually or via IR power key)"
echo "    turret-remote.service: $(systemctl is-active turret-remote 2>/dev/null) / $(systemctl is-enabled turret-remote 2>/dev/null)"
echo "    pi-turret-ir.service:  $(systemctl is-active pi-turret-ir 2>/dev/null) / $(systemctl is-enabled pi-turret-ir 2>/dev/null)"
