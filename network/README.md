# pi-turret — Deployment WiFi (report for future fixes)

The turret's remote reachability (SSH, Tailscale, the web UI + stream) rides on a **flaky WiFi
situation** at the deployment site. This doc is the as-built record + the playbook for changing it
safely without locking yourself out. Last updated **2026-07-01**.

Secrets (SSID passwords, Pi login) live in `.claude/.env` (gitignored). SSIDs, RFC1918 IPs and the
Pi MAC below are not secret.

---

## TL;DR — current state (2026-07-01)

- **Reach the Pi over Tailscale**: `ssh pi` → `pi-jayson` / `100.125.7.98`. `tailscaled` is
  **enabled at boot** — this is the lifeline: as long as the Pi gets *any* internet, SSH works
  regardless of which SSID/subnet it's on.
- **Currently connected to `wix.guest`** → `10.0.0.27/22`, gw `10.0.0.1`, internet OK.
- **wpa_supplicant priority:** `MikroTik-EC74D5` = **primary** (priority 20), `wix.guest` = **backup**
  (priority 10). Both enabled. `wix-repeater` was removed (broken). `1C061B`/`AP7026` left disabled.
- **dhcpcd = plain DHCP** (no static IP anywhere). The old static hack is gone.
- **A connectivity watchdog** (`wifi-watchdog.timer`, every 60 s + 45 s after boot) keeps the Pi
  reachable: if wlan0 is ever associated-but-internetless, it escapes to `wix.guest`.
- ⚠️ **MikroTik-EC74D5 is in range (strong, -36 dBm) but currently does NOT associate.** With it as
  strict primary, each boot has a short offline window (Pi tries MikroTik → watchdog escapes to
  `wix.guest`, ~45–90 s) until MikroTik is fixed. It self-heals; it does not lock you out.

---

## Topology & the ".88 problem"

A **MikroTik hAP2** (SSID `MikroTik-EC74D5`, 2.4 GHz) bridges the Pi to a larger `wix.*` office
hotspot. Two DHCP servers race on that bridged L2:

| DHCP server | Hands out | Gateway | Internet? |
|---|---|---|---|
| MikroTik's **own** `192.168.88.1` (rogue) | `192.168.88.x/24` | `192.168.68.1` (off-subnet → unroutable) | **NO** (dead-end) |
| upstream hotspot `192.168.68.1` | `192.168.68.x/24` | `192.168.68.1` | **YES** |

On boot the client took whichever DHCP **OFFER** arrived first → "half the time gets `.88` and no
internet." Proof (dhcpcd lease dump, 2026-07-01): `dhcp_server_identifier=192.168.88.1`,
`ip_address=192.168.88.230`, `routers=192.168.68.1`. The MikroTik admin (`192.168.88.1`) is **not
reachable** from the `.68` side without a `/16` mask or an explicit route.

`wix.guest` is a **different network entirely**: subnet `10.0.0.0/22`, gw `10.0.0.1`, its own DHCP,
has internet. Being a *guest* SSID it is **likely client-isolated** (see UI-access note below).

---

## ⚠️ Hard lesson — do NOT hard-static this hotspot

**On 2026-07-01, setting `static ip_address=192.168.68.145/24` in `/etc/dhcpcd.conf` (no DHCP)
killed internet + Tailscale and required a PHYSICAL trip to the Pi to recover.**

Why: this hotspot requires the client to **speak DHCP** to stay authorized. A hard static sends no
DHCP at all → the AP stops passing traffic. The older *working* hack used `inform 192.168.68.145`
(SSID-scoped), a **soft** static: it pins `.68.145` but **still does a DHCP INFORM**, so it stays
authorized. `inform` is fragile (sometimes still honors the `.88` lease) but does **not** lock you
out. Plain DHCP (current) is simplest and also fine.

**Golden rules for any remote wlan0 change:**
1. **Keep DHCP alive.** Never a no-DHCP hard static on this hotspot.
2. Prefer **non-disruptive live `wpa_cli`** (`add_network`/`enable_network`/`set_network priority`/
   `save_config`) — these do **not** drop the current link.
3. If you must restart `dhcpcd`/`wpa_supplicant`, arm a **detached auto-rollback that survives a
   reboot.** A `/run`-based `systemd-run --on-active` timer does **NOT** survive a reboot — that is
   why the earlier rollback didn't save us (the owner rebooted). Use a persistent unit or the
   watchdog below.
4. `wpa_supplicant` does **not** auto-roam off an AP that is *associated but internetless* — that's
   exactly why the watchdog exists.

---

## As-built configuration

### dhcpcd — plain DHCP
`/etc/dhcpcd.conf` has no static block (only a harmless empty `SSID MikroTik-EC74D5` line). Leave it.
Staged for later: `/etc/dhcpcd.conf.dhcp-normal` (a clean pure-DHCP variant).

### wpa_supplicant — priorities
`/etc/wpa_supplicant/wpa_supplicant.conf` (`update_config=1`, PSKs redacted):
```
network={ ssid="MikroTik-EC74D5" key_mgmt=WPA-PSK priority=20 }   # primary
network={ ssid="wix.guest"       key_mgmt=WPA-PSK priority=10 }   # backup
network={ ssid="1C061B"  disabled=1 }   network={ ssid="AP7026" disabled=1 }
```
Higher priority = preferred. Both `MikroTik` and `wix.guest` use the same password (`Welcome2Wix!`,
in `.env`). To flip which is preferred, swap the two `priority=` numbers and `save_config`.

### Connectivity watchdog (the safety net)
Runs on the Pi, independent of connectivity. If wlan0 has no internet:
- **already on `wix.guest`** → gentle `dhcpcd` renew only (fixes a bad lease; no re-associate).
- **on any other SSID** (e.g. MikroTik with a dead `.88` lease, or stuck unassociated) → switch to
  `wix.guest`, poll for a lease, with a `dhcpcd restart` fallback.

It is **runtime-only** (never runs `save_config`), so a reboot still retries the configured primary
(MikroTik). Files (all on the Pi; mirrored in this repo under `network/`):

`/usr/local/sbin/wifi-watchdog.sh`:
```sh
#!/bin/sh
# wifi-watchdog: keep the Pi reachable. On no-internet:
#  - already on the known-good SSID -> renew DHCP only; never re-associate.
#  - on a different SSID (e.g. MikroTik with a dead .88 lease) -> switch to the known-good SSID.
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
```

`/etc/systemd/system/wifi-watchdog.service`:
```ini
[Unit]
Description=WiFi connectivity watchdog (escape to known-good SSID if no internet)
After=multi-user.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wifi-watchdog.sh
```
`/etc/systemd/system/wifi-watchdog.timer`:
```ini
[Unit]
Description=Run wifi-watchdog periodically
[Timer]
OnBootSec=45
OnUnitActiveSec=60
AccuracySec=10s
Unit=wifi-watchdog.service
[Install]
WantedBy=timers.target
```
Enable: `sudo systemctl daemon-reload && sudo systemctl enable --now wifi-watchdog.timer`.

Inspect / operate:
```bash
tail -f /var/log/wifi-watchdog.log          # what it's doing
systemctl list-timers wifi-watchdog.timer   # next run
sudo systemctl stop  wifi-watchdog.timer    # pause it (e.g. before manual net surgery)
sudo systemctl start wifi-watchdog.timer    # resume
```

**Change the "good" SSID:** edit `GOOD_SSID` in the script. It must be an enabled network in
`wpa_supplicant.conf` that reliably has internet.

---

## MikroTik is currently unassociable — what to do

Symptom (verified 2026-07-01): forcing the Pi to MikroTik leaves it at `ssid=''` (disconnected) for
25 s+ instead of connecting, then it falls back / the watchdog escapes to `wix.guest`. The AP is
broadcasting strongly, so this is an **association failure**, not just the `.88` lease issue
(possible causes: AP in a bad state, changed auth, TKIP/handshake). This matches the owner's
"couldn't connect to MikroTik this time."

**Check if MikroTik is associable again** (safe, on-Pi):
```bash
sudo wpa_cli -i wlan0 scan >/dev/null; sleep 4
sudo wpa_cli -i wlan0 scan_results | grep -i MikroTik   # is it in range?
# then a guarded association test (keeps wix.guest as fallback, watchdog running):
sudo wpa_cli -i wlan0 reassociate                       # wpa will prefer MikroTik (prio 20)
for i in 1 2 3 4 5 6; do sleep 5; sudo wpa_cli -i wlan0 status | grep -E '^ssid=|^ip_address='; done
```
- If it lands on `MikroTik-EC74D5` with a `192.168.68.x` IP and internet → MikroTik works again;
  primary is doing its job. Local-LAN UI access via `http://192.168.68.x:8001` resumes.
- If it grabs a `192.168.88.x` lease → the watchdog will escape to `wix.guest` within ~60 s.
- If it stays `ssid=''` → still unassociable; consider making `wix.guest` primary meanwhile (below).

**If you'd rather not have the boot delay while MikroTik is broken** — make `wix.guest` the preferred
network (instant boot online), MikroTik the fallback:
```bash
MK=$(sudo wpa_cli -i wlan0 list_networks | awk -F'\t' '$2=="MikroTik-EC74D5"{print $1}')
WG=$(sudo wpa_cli -i wlan0 list_networks | awk -F'\t' '$2=="wix.guest"{print $1}')
sudo wpa_cli -i wlan0 set_network $WG priority 20
sudo wpa_cli -i wlan0 set_network $MK priority 10
sudo wpa_cli -i wlan0 save_config
```
Trade-off: MikroTik won't be auto-preferred when it recovers (you'd flip the numbers back).

---

## Keeping the same SSID/password reachable after the MikroTik is fixed

The SSID (`MikroTik-EC74D5`) and password stay in `wpa_supplicant.conf` unchanged, so **association
resumes automatically** once the AP is healthy — nothing to re-enter. Two cases once fixed:

- **Fix = disable the rogue `.88` DHCP / add a `.68` reservation** → nothing to do on the Pi. Plain
  DHCP + MikroTik-primary will just get a good `.68` lease. (Optional: if you want a *fixed* Pi IP,
  add a **DHCP reservation on the MikroTik** for MAC `d8:3a:dd:19:2b:49`, and make sure that IP is
  outside the DHCP pool.)
- You never need the no-DHCP hard static again. If some tool re-adds one, replace with the staged
  `sudo cp /etc/dhcpcd.conf.dhcp-normal /etc/dhcpcd.conf && sudo systemctl restart dhcpcd`.

---

## The real fix (server-side, when you can reach the MikroTik admin)

Client-side workarounds only paper over it. The cure is on the hAP2 (Winbox/WebFig at
`192.168.88.1` — reachable from the Pi only with a `/16` mask or a route, or from a device on the
MikroTik LAN):
- **Make it a pure bridge** — disable the MikroTik's own DHCP server so only the upstream `.68`
  server answers; **or**
- **Add a static DHCP lease (reservation)** binding the Pi MAC `d8:3a:dd:19:2b:49` → a fixed
  `192.168.68.x`.

Needs MikroTik admin creds (not in `.env`) and care — a wrong bridge/DHCP change can break the
hotspot for everything behind it.

---

## Reboot test (recommended, but it's the one risky step)

The watchdog is proven at runtime, and `wifi-watchdog.timer` is enabled, so a reboot *should* come
back online (MikroTik if it recovers, else auto-escape to `wix.guest` within ~45–90 s). **Caveat:**
no detached rollback survives a reboot — boot recovery relies solely on the watchdog. Do this when a
short outage is acceptable / someone can reach the Pi if needed:
```bash
ssh pi sudo reboot
# wait ~2 min, then:
ssh pi 'ip -4 addr show wlan0 | grep inet; tail -n 5 /var/log/wifi-watchdog.log; ping -c2 8.8.8.8'
```

---

## Troubleshooting playbook

| Symptom | Do this |
|---|---|
| `ssh pi` times out | Wait ~90 s (watchdog escape window after a bad boot). Still down after ~3 min → physical access. |
| On a `192.168.88.x` IP, no internet | Watchdog should escape within 60 s. Manual: `sudo wpa_cli -i wlan0 select_network <wix.guest id>`. |
| On `wix.guest` but flaky | It's a guest net; check `tail /var/log/wifi-watchdog.log`. UI over Tailscale, not LAN. |
| Need local-LAN UI (`192.168.68.x`) | Only when MikroTik associates. Otherwise use Tailscale UI. |
| Doing manual net surgery | `sudo systemctl stop wifi-watchdog.timer` first, re-`start` after. |

---

## Access reference

- **SSH:** `ssh pi` (Tailscale). Backup: `ssh -i $MAC_SSH_KEY_LOCALTION jayson@pi-jayson`.
- **Turret web UI + stream:** the service is **manual-start** (`sudo systemctl start turret`, boots
  DISARMED). Then reach it at **`http://100.125.7.98:8001`** (Tailscale — always works) or
  `http://<lan-ip>:8001` (only on a non-isolated LAN, i.e. MikroTik, not `wix.guest`).
- **On-Pi backups:** `/etc/dhcpcd.conf.bak.2026-07-01_201641`,
  `/etc/wpa_supplicant/wpa_supplicant.conf.bak.2026-07-01_{201641,205613}`,
  `/etc/dhcpcd.conf.dhcp-normal`.
