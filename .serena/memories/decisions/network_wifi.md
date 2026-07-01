# mem:decisions/network_wifi — deployment-site WiFi (MikroTik hAP2 + wix hotspot)

The Pi's remote reachability (SSH / Tailscale / turret UI) rides on flaky deployment WiFi.
Reach the Pi via Tailscale (`ssh pi`, 100.125.7.98) — works over ANY internet path regardless of subnet.
**Full ops report + playbooks + watchdog artifacts: repo `network/README.md`.** Creds (SSIDs, PW
`Welcome2Wix!`, Pi MAC d8:3a:dd:19:2b:49) in `.claude/.env` (gitignored).

## The ".88" problem (root cause, verified on-Pi 2026-07-01)
- MikroTik hAP2 (`MikroTik-EC74D5`, 2.4GHz) bridges the Pi to a larger "wix.*" hotspot.
- TWO DHCP servers race on that bridged L2: the MikroTik's OWN `192.168.88.1` (dead-end — 192.168.88.x
  with off-subnet .68.1 gw → NO internet) vs upstream `192.168.68.0/24` (gw .68.1 → HAS internet).
  Pi takes whichever OFFER lands first → "half the time gets .88". Proof: lease dump
  dhcp_server_identifier=192.168.88.1, ip_address=192.168.88.230, routers=192.168.68.1.

## CRITICAL LESSON — never no-DHCP hard-static this hotspot (caused a lockout + physical trip 2026-07-01)
- `static ip_address=192.168.68.145/24` (no DHCP) KILLED internet+Tailscale; recovery needed PHYSICAL
  access. The hotspot needs the client to SPEAK DHCP to stay authorized. Old working hack = SSID-scoped
  `inform` (soft static: pins IP but still DHCP-INFORMs). Plain DHCP is fine too.
- Remote-net-change rules: keep DHCP alive; prefer non-disruptive live `wpa_cli`
  (enable_network/set_network priority/save_config — never drops the link); a `/run` `systemd-run`
  rollback does NOT survive a reboot. wpa_supplicant does NOT auto-roam off an associated-but-
  internetless AP (that's why the watchdog exists).

## As-built (2026-07-01)
- dhcpcd = plain DHCP (owner reverted the static; only a harmless empty `SSID MikroTik-EC74D5` block).
  Staged `/etc/dhcpcd.conf.dhcp-normal` for post-fix use.
- wpa_supplicant (update_config=1): **MikroTik-EC74D5 priority 20 = primary, wix.guest priority 10 =
  backup, both enabled** (same PW). `wix-repeater` REMOVED (broken). `1C061B`/`AP7026` disabled.
  wix.guest is a GUEST subnet 10.0.0.0/22 (gw 10.0.0.1) — likely client-isolated → turret UI via
  Tailscale (100.125.7.98:8001), maybe not over that LAN; MikroTik (.68.x) gives local-LAN UI.
- **Connectivity watchdog** (the safety net): `/usr/local/sbin/wifi-watchdog.sh` +
  `wifi-watchdog.{service,timer}` (OnBootSec=45, OnUnitActiveSec=60, ENABLED). Offline → if on
  wix.guest, DHCP-renew only; else escape to wix.guest (select_network + poll + dhcpcd-restart
  fallback). Runtime-only (no save_config) so reboots retry the primary. Proven on-Pi: no-op-when-
  online, non-disruptive renew, and a real auto-escape (21:13:56 ssid='' → 21:14:06 recovered on
  wix.guest). Repo mirror: `network/wifi-watchdog.*`. Log: /var/log/wifi-watchdog.log.
  Change the good SSID via `GOOD_SSID=` in the script. Pause during manual surgery:
  `systemctl stop wifi-watchdog.timer`.

## Open findings
- **MikroTik-EC74D5 currently will NOT associate** (in range -36 dBm but sits ssid='' 25s+), beyond
  the .88 issue — matches owner "couldn't connect this time". So MikroTik-primary = ~45-90s watchdog-
  covered offline window each boot until the AP is fixed (self-heals, no lockout). To avoid the delay
  meanwhile: swap priorities so wix.guest=20/MikroTik=10 (see network/README.md).
- REAL fix = server-side on the MikroTik: disable its rogue 88 DHCP (pure bridge) or reserve Pi MAC →
  fixed .68.x. Needs MikroTik admin creds (not in .env). SSID+PW stay same → association auto-resumes.
- Reboot test not yet run (only boot-time recovery is the watchdog — no rollback survives reboot).
Related: `mem:project/machine_access`, `mem:decisions/monitoring`.