# mem:decisions/ir_remote — IR remote control (Step 1.15), built + verified on-Pi 2026-06-30

Architecture: **separate always-on SUPERVISOR daemon** (owner's choice, twice-stated). NOT the
in-process listener — only one process can `EVIOCGRAB` the IR device, and only an always-on process
can `systemctl start turret.service` while the app is STOPPED. Related: `mem:architecture/wiring`, `mem:core`.

## What runs
- **`turret-remote.service`** (root, **enabled on boot**) = `remote_daemon.py` → `app/remote_supervisor.py`.
  Owns `/dev/input/eventN` (evdev, EVIOCGRAB), maps NEC `KEY_*` → intents:
  - **POWER key `0`** (`KEY_NUMERIC_0`) → `systemctl start/stop turret.service` (toggles on unit state).
  - every other key → HTTP POST to the running app's **existing** :8001 API (`/api/cmd`, `/api/control-cmd`).
  - never touches PCA9685/pump/servos. Best-effort (every dispatch + loop try/excepted). evdev lazy → imports on Mac.
- **`pi-turret-ir.service`** (oneshot, enabled) = `monitoring/ir-load-keytable.sh` → resolves the rc device
  **by name `gpio_ir_recv`** (rcN index drifts) and loads `/etc/rc_keymaps/pi_turret.toml` (NEC, `-D150 -P110`).
- The in-process `app/remote.py` listener is **dormant** — `main.py` no longer starts it (removed `RemoteListener`
  wiring + `TurretRemoteActions`), so `remote.enabled=true` activates only the supervisor.

## Added 2026-07-01 — standby LCD + remote Pi-shutdown (Mac-tested; on-Pi UNVERIFIED)
- **SHUTDOWN = digit `9`** (`KEY_NUMERIC_9`, scancode 0x4a; was a spare). `key_shutdown` → `SHUTDOWN` intent →
  `IntentForwarder._shutdown_pi()` runs `systemctl poweroff` **only when `_unit_active()` is False** (refused while
  turret runs; oneshot so autorepeat can't double-fire). Footgun: one press in standby halts the Pi (physical
  power-cycle to return) — gate is "turret off"; offer hold-to-confirm if owner wants.
- **Supervisor owns the shared 1602 LCD while the turret is OFF.** `SupervisorLcd` (low-rate thread) shows a STANDBY
  screen (`format_standby_lines`: "SUPERVISOR <spin>" / "0:PWR-ON 9:HALT") **only when `turret.service` is inactive**;
  polls `forwarder.unit_active()` every `remote.lcd_poll_s` (2.0s), writes on frame-change → one self-healing
  hand-off frame at most. The turret app's `LcdReporter` reclaims the LCD whenever it runs (is-active flips to
  "active" as soon as the unit execs, *before* the app inits its LCD → contention window is tiny). Gated on
  `app.lcd_enabled && remote.lcd_status_enabled`. `flash()` freezes the screen for the shutdown confirm.
- **NEW GOTCHA:** `rpi_lcd` is **pip-only** and the supervisor runs as **ROOT** → it must be installed system-wide
  (`sudo pip3 install rpi_lcd`, now in deploy.sh, best-effort). Same class as the python3-yaml/-evdev need.
  `StatusLcd` degrades to no-display if it's missing (no crash, no standby screen).
- New `RemoteConfig` fields: `key_shutdown`, `lcd_status_enabled`, `lcd_poll_s` (config.py + config.yaml). Tests: 245.
- **On-Pi TODO:** verify root can drive the LCD + `systemctl poweroff`; the standby/hand-off render; "SHUTTING DOWN"
  lands before power cuts. Re-run `monitoring/deploy.sh` (installs rpi_lcd) after `git push pi main`.

## Hardware (Pi-verified)
- **Pin = BCM25 / GPIO25 / physical pin 22** (owner-wired; supersedes the old BCM17 proposal).
  `dtoverlay=gpio-ir,gpio_pin=25` in **`/boot/config.txt`** (Bullseye, NOT /boot/firmware). dmesg: `ir-receiver@19`
  (0x19=25); rc0 = `gpio_ir_recv`, `/dev/lirc0`. Pin idles HIGH (internal pull-up on).
- Protocol = **NEC**. Verified scancodes on THIS 21-key remote (match the documented table):
  CH- `0x45`, CH+ `0x47`, CH `0x46`, EQ `0x09`, ▶∥ `0x43`, ∣◀◀ `0x44`, ▶▶∣ `0x40`, `+` `0x15`, `-` `0x07`, `0` `0x16`;
  digits 1-9 = 0x0c/18/5e/08/1c/5a/42/52/4a.

## Key→intent map (config.py RemoteConfig defaults == /etc/rc_keymaps/pi_turret.toml KEY_* names)
KEY_STOP→ESTOP(pump_off+disarm), KEY_CHANNELUP→ARM_TOGGLE(reads /api/turret-state), KEY_MODE→TOGGLE_FIRE_ENABLE,
KEY_HOMEPAGE→HOME(center), KEY_PLAYPAUSE→FIRE, KEY_NUMERIC_0→POWER_TOGGLE, KEY_PREVIOUS/NEXT→JOG_PAN-/+,
KEY_VOLUMEUP/DOWN→JOG_TILT+/-. Jog → `/api/control-cmd` which the app accepts **only when DISARMED**
(MANUAL-while-armed deferred). Ships `remote.enabled=False`; the Pi overlay sets it true.

## GOTCHAS (cost real time — don't rediscover)
- **The root supervisor needs `python3-yaml` AND `python3-evdev` installed SYSTEM-WIDE (apt).** PyYAML was only
  in jayson's `~/.local` (pip), so root's `load_config()` silently fell back to defaults (`enabled=False`) and the
  daemon exited "idle". `config._load_yaml` swallows ImportError. `turret.service` works because it runs as jayson.
  Fix: `sudo apt install python3-yaml python3-evdev ir-keytable` (deploy.sh now ensures these).
- **`systemctl stop turret.service` blocks ~5 s** (clean disarm + TimeoutStopSec). The supervisor's dispatch is
  synchronous, so it's unresponsive during a stop — don't mash POWER (each queued press toggles).
- **Xorg + triggerhappy (thd) also read event0** → without the grab, remote digits leak into X as keystrokes.
  `grab=True` (EVIOCGRAB) makes the supervisor the exclusive reader. Keep it on.
- **`-p nec` decodes fine** — earlier empty captures were just press-timing, not a protocol problem.
- deploy.sh `[ -f /etc/alloy/secrets.env ]` ran as jayson but `/etc/alloy` is root-only → false-negative; use `sudo test -f`.

## Verified on-Pi 2026-06-30
POWER press → `systemctl start/stop turret.service` (observed). HTTP forwards via the supervisor's own
`IntentForwarder.dispatch` against the live app: TOGGLE_FIRE_ENABLE flips `fire_enabled`, ARM_TOGGLE flips
state Disabled↔Enabled, HOME ok. `turret-remote.service` active+enabled; matched by Alloy `unit_include
"(turret.*|alloy)\.service"` → liveness on the pi-health "Services" row (re-import the dashboard JSON to see it).

## Files
`app/remote_supervisor.py`, `remote_daemon.py`, `config.py`+`config.yaml` (RemoteConfig), `main.py` (listener removed),
`monitoring/systemd/{turret-remote,pi-turret-ir}.service`, `monitoring/ir-load-keytable.sh`,
`monitoring/rc_keymaps/pi_turret.toml`, `monitoring/deploy.sh`, `monitoring/dashboards/generate_dashboards.py`+`pi-health.json`,
`tests/test_remote_supervisor.py` (28). Plan: `IMPLEMENTATION_PLAN.md §1.15`; design (superseded in-process bits): `plans/moniotoring-and-remote/ir-remote-integration-plan.md`.
