#!/usr/bin/env bash
# Resolve the gpio-ir rc device BY NAME (the rcN index drifts — vc4-hdmi CEC also
# registers an rc device) and load the pi-turret NEC keytable onto it. Run by
# pi-turret-ir.service at boot; safe to run by hand for debugging.
#
#   sudo monitoring/ir-load-keytable.sh [/etc/rc_keymaps/pi_turret.toml]
#
# Env overrides: IR_REPEAT_DELAY_MS (-D), IR_REPEAT_PERIOD_MS (-P) for hold-to-slew jog.
set -euo pipefail

KEYMAP="${1:-/etc/rc_keymaps/pi_turret.toml}"
DELAY="${IR_REPEAT_DELAY_MS:-150}"
PERIOD="${IR_REPEAT_PERIOD_MS:-110}"

if [ ! -r "$KEYMAP" ]; then
  echo "ir-load-keytable: keymap not found: $KEYMAP" >&2
  exit 1
fi

# Find the rc node whose driver/name is the gpio-ir receiver (not vc4 CEC).
rc=""
for d in /sys/class/rc/rc*; do
  [ -e "$d" ] || continue
  drv=""
  if [ -L "$d/device/driver" ]; then
    drv="$(basename "$(readlink -f "$d/device/driver")" 2>/dev/null || true)"
  fi
  nm="$(cat "$d"/input*/name 2>/dev/null || true)"
  if printf '%s %s' "$drv" "$nm" | grep -qiE 'gpio[_-]?ir'; then
    rc="$(basename "$d")"
    break
  fi
done

if [ -z "$rc" ]; then
  echo "ir-load-keytable: gpio-ir rc device not found (is dtoverlay=gpio-ir,gpio_pin=25 set + rebooted?)" >&2
  exit 1
fi

echo "ir-load-keytable: loading $KEYMAP onto $rc (NEC, -D $DELAY -P $PERIOD)"
exec ir-keytable -s "$rc" -c -p nec -w "$KEYMAP" -D "$DELAY" -P "$PERIOD"
