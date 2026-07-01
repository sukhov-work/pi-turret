#!/bin/sh
# wifi-watchdog: keep the Pi reachable at the deployment site (see network/README.md).
# On no-internet:
#  - already on the known-good SSID -> renew DHCP only (fix a bad lease); never re-associate.
#  - on a different SSID (e.g. MikroTik with a dead .88 lease) -> switch to the known-good SSID.
# Runtime-only (never runs save_config) so a reboot still retries the configured primary.
# Deployed to: /usr/local/sbin/wifi-watchdog.sh  (run by wifi-watchdog.timer)
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/usr/bin:/bin
IFACE=wlan0
GOOD_SSID="wix.guest"
LOG=/var/log/wifi-watchdog.log
ts() { date '+%F %T'; }
online() {
  ping -c1 -W3 8.8.8.8 >/dev/null 2>&1 && return 0
  ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 && return 0
  return 1
}
poll_online() { _t=0; while [ "$_t" -lt "$1" ]; do online && return 0; sleep 5; _t=$((_t+5)); done; return 1; }

online && exit 0
sleep 5
online && exit 0

CUR=$(wpa_cli -i "$IFACE" status 2>/dev/null | sed -n 's/^ssid=//p')
echo "$(ts) OFFLINE (current SSID='$CUR')" >> "$LOG"
if [ "$CUR" = "$GOOD_SSID" ]; then
  echo "$(ts)  on good SSID; renewing DHCP (no re-associate)" >> "$LOG"
  dhcpcd -n "$IFACE" >> "$LOG" 2>&1 || true
  if poll_online 20; then echo "$(ts)  recovered via DHCP renew" >> "$LOG"
  else echo "$(ts)  still offline on good SSID (site/AP outage?) - no switch" >> "$LOG"; fi
  exit 0
fi
GID=$(wpa_cli -i "$IFACE" list_networks 2>/dev/null | awk -F '\t' -v s="$GOOD_SSID" '$2==s{print $1; exit}')
if [ -z "$GID" ]; then echo "$(ts)  ERROR: good SSID '$GOOD_SSID' not configured" >> "$LOG"; exit 0; fi
echo "$(ts)  escaping '$CUR' -> '$GOOD_SSID' (id=$GID)" >> "$LOG"
wpa_cli -i "$IFACE" select_network "$GID" >> "$LOG" 2>&1
if poll_online 40; then echo "$(ts)  recovered on '$GOOD_SSID' ip=$(ip -4 -o addr show "$IFACE" | awk '{print $4}')" >> "$LOG"; exit 0; fi
echo "$(ts)  no lease after switch; restarting dhcpcd" >> "$LOG"
systemctl restart dhcpcd >> "$LOG" 2>&1 || true
if poll_online 25; then echo "$(ts)  recovered after dhcpcd restart ip=$(ip -4 -o addr show "$IFACE" | awk '{print $4}')" >> "$LOG"
else echo "$(ts)  STILL offline after escape+restart" >> "$LOG"; fi
