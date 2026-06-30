# pi-turret v2 — Step 1.15 IR Remote Control — AS-BUILT

> **Status: BUILT + VERIFIED on-Pi (2026-06-30).** This was a "plan to build" doc; it now records
> what shipped. Authority for the as-built design is `IMPLEMENTATION_PLAN.md §1.15`; durable on-Pi
> truth + gotchas live in Serena `mem:decisions/ir_remote`; chronology in `DECISIONS.md` (2026-06-30).
> The original research report ("Integrating a 21-Key NEC IR Remote on Pi 4 / Bullseye") holds the
> deep *why* (decode-stack comparison, NEC timing). This file = the buildable + verified essentials.

---

## 1. Architecture — separate SUPERVISOR daemon (not an in-process listener)

The owner wanted a separate process to **manage the main turret app** (turn it on/off). Two hard facts
forced the architecture:
- Only **one** process can `EVIOCGRAB` the IR `/dev/input/eventN` device.
- Only an **always-on** process can `systemctl start turret.service` while the app is STOPPED.

So IR lives in an always-on supervisor, **not** an in-process thread:

```
21-key NEC remote
      │  (IR)
      ▼
VS1838B on BCM25 ──► kernel gpio-ir + rc-core ──► /dev/input/eventN  (KEY_* events, NEC keytable)
                                                        │  (EVIOCGRAB, exclusive)
                                                        ▼
                              turret-remote.service  (root, enabled on boot)
                              = remote_daemon.py → app/remote_supervisor.py
                                    │                         │
                   POWER key "0" ───┘                         └─── every other key
                          │                                            │
                          ▼                                            ▼
              systemctl start/stop                       HTTP POST → running app :8001
                turret.service                           /api/cmd · /api/control-cmd
                (the main app)                           (the routes the web UI already uses)
```

The supervisor **never** touches PCA9685/pump/servos — the app's control thread stays the single mover.
It only starts/stops the unit and POSTs intents the app already exposes. Everything is best-effort
(every dispatch + the read loop is try/excepted; a remote/app/systemctl fault never crashes anything).
`evdev` is imported lazily, so the module imports + unit-tests on the Mac.

The in-process `app/remote.py` listener (`RemoteActions`/`build_key_map`/`RemoteListener`) is kept as a
**dormant seam** but is **no longer wired in `main.py`** — `remote.enabled=true` activates only the
supervisor (two processes can't both own the device).

### Intent semantics (`app/remote_supervisor.py`)
| Intent | Action |
|---|---|
| `POWER_TOGGLE` | `systemctl is-active` → `start` if stopped else `stop` `turret.service` |
| `ARM_TOGGLE` | `GET /api/turret-state` → POST `arm` or `disarm` to `/api/cmd` |
| `TOGGLE_FIRE_ENABLE` | POST `toggle_fire` |
| `HOME` | POST `center` |
| `FIRE` | POST `fire_now` |
| `ESTOP` | POST `pump_off` **then** `disarm` (de-energize first) |
| `JOG_PAN_±` / `JOG_TILT_±` | POST `left/right/up/down` to `/api/control-cmd` — **accepted only when DISARMED** |

`http_plan(intent)` (pure, Mac-tested) builds the static POSTs; `ARM_/POWER_` toggles read live state first.

---

## 2. Hardware (final, verified)

- **Receiver:** bare **VS1838B** (the breakout PCB was lost — not needed). Pin order **SIGNAL · GND · VCC**
  (front view, lens toward you, legs down — standard datasheet order; back side mirrors).
- **Signal → BCM25 / GPIO25 / physical pin 22.** `dtoverlay=gpio-ir,gpio_pin=25` in **`/boot/config.txt`**
  (Bullseye — *not* `/boot/firmware/`). Verified: dmesg `ir-receiver@19` (0x19 = 25); `rc0 = gpio_ir_recv`,
  `/dev/lirc0`; BCM25 idles HIGH (overlay enables the internal pull-up).
- **VCC → 3V3 via a replacement RC supply filter** (the breakout's filter): **100 Ω series in VCC** +
  **0.1 µF** to GND + **4.7–10 µF** bulk to GND. 3.3 V is in spec; OUT swings 0–3.3 V → MCU-safe, no level
  shifter. First-power sanity (reversed VCC/GND silently kills the part): OUT idles HIGH, dips LOW on a press.
- Protocol = **NEC**. No external pull-up needed.

---

## 3. Decode stack — `gpio-ir` + rc-core + `ir-keytable` + python-evdev

Kernel decodes NEC in-ISR; the remote appears as `/dev/input/eventN`; the supervisor reads `KEY_*` via
evdev. Daemon-free, negligible CPU. (LIRC's GPIO path is deprecated; pigpio needs root for DMA — both rejected.)

The keytable maps scancodes → `KEY_*`; `RemoteConfig` maps `KEY_*` → intent (config.py defaults ==
`/etc/rc_keymaps/pi_turret.toml`). Loaded at boot by **`pi-turret-ir.service`** (oneshot) →
**`monitoring/ir-load-keytable.sh`**, which resolves the rc device **by name `gpio_ir_recv`** (the rcN
index drifts — vc4-hdmi CEC also registers rc devices) and runs
`ir-keytable -s rcN -c -p nec -w <toml> -D 150 -P 110`.

---

## 4. Scancode reference — VERIFIED on this unit (2026-06-30)

`ir-keytable -t` prints the bare NEC command byte (address `0x00`). Captured + read-back confirmed all 19
map correctly; the mapped controls match the documented table exactly.

| Button | scancode → KEY_* | Button | scancode → KEY_* |
|---|---|---|---|
| CH− | `0x45` → KEY_STOP | `−` | `0x07` → KEY_VOLUMEDOWN |
| CH | `0x46` → KEY_HOMEPAGE | `+` | `0x15` → KEY_VOLUMEUP |
| CH+ | `0x47` → KEY_CHANNELUP | EQ | `0x09` → KEY_MODE |
| ∣◀◀ | `0x44` → KEY_PREVIOUS | `0` | `0x16` → KEY_NUMERIC_0 |
| ▶▶∣ | `0x40` → KEY_NEXT | 1/2/3 | `0x0c`/`0x18`/`0x5e` (spare) |
| ▶∥ | `0x43` → KEY_PLAYPAUSE | 4–9 | `0x08`/`1c`/`5a`/`42`/`52`/`4a` (spare) |

Recapture if ever needed: `sudo ir-keytable -s rcN -c -p nec -t`, press each button. (Earlier empty
captures were just press-timing — `-p nec` decodes fine.)

---

## 5. Button → intent map (as-built)

| Button | `KEY_*` | Intent |
|---|---|---|
| **CH−** | KEY_STOP | **ESTOP** (pump off + disarm) |
| CH+ | KEY_CHANNELUP | ARM / DISARM |
| CH | KEY_HOMEPAGE | HOME / center |
| EQ | KEY_MODE | toggle fire-enable (would-fire ↔ live) |
| **▶∥** | KEY_PLAYPAUSE | **FIRE** |
| ∣◀◀ / ▶▶∣ | KEY_PREVIOUS / KEY_NEXT | jog pan − / + (disarmed only) |
| − / + | KEY_VOLUMEDOWN / KEY_VOLUMEUP | jog tilt − / + (disarmed only) |
| **0** | KEY_NUMERIC_0 | **POWER**: `systemctl start/stop turret.service` |
| 1–9, 100+, 200+ | (mapped, unused) | spare (future presets) |

---

## 6. `RemoteConfig` (config.py / config.yaml — as-built)

Scalar `key_*` string fields (UI-safe — no dict to break the config editor) + supervisor operational
fields. Py3.9 (no `match`, no `X | Y`). Ships `enabled=False`; the Pi's `config.local.yaml` sets it true.
Key fields: `enabled`, `gpio_bcm=25`, `device_name="gpio_ir_recv"`, `input_device`, `grab=True`,
`oneshot_ignore_autorepeat=True`, the ten `key_*` bindings, `forward_host/forward_port=8001/forward_timeout_s`,
`turret_unit="turret.service"`, `repeat_delay_ms/repeat_period_ms`. Full descriptions in `PARAMETERS.md`.

---

## 7. On-Pi bring-up runbook (what was actually done)

```bash
# 1. deps the ROOT supervisor needs system-wide (deploy.sh now ensures these):
sudo apt install -y ir-keytable python3-evdev python3-yaml

# 2. overlay + reboot (Bullseye = /boot/config.txt):
echo 'dtoverlay=gpio-ir,gpio_pin=25' | sudo tee -a /boot/config.txt && sudo reboot

# 3. confirm device (resolve by NAME, index drifts):
ir-keytable                       # expect: Driver: gpio_ir_recv ... /dev/input/eventN, /dev/lircN

# 4. capture/verify scancodes (default keymap is rc6-mce → NEC decodes to nothing until -p nec):
sudo ir-keytable -s rcN -c -p nec -t

# 5. deploy: commit on the Mac, then:
git push pi main && ssh pi '~/pi-turret/monitoring/deploy.sh'   # installs+enables both units, ensures deps
#    set remote.enabled: true in ~/pi-turret/config.local.yaml on the Pi, then:
sudo systemctl restart turret-remote.service
```

`deploy.sh` installs `turret-remote.service` + `pi-turret-ir.service` + `/etc/rc_keymaps/pi_turret.toml`,
ensures the apt deps, and `enable --now`s both units. The supervisor runs as **root** (needs `/dev/input`
**and** `systemctl`); least-privilege alternative noted in the unit file (jayson + a polkit rule).

**Verified on the rig:** POWER `0` → supervisor logs `KEY_NUMERIC_0 → POWER_TOGGLE` → `systemctl start/stop
turret.service`; the supervisor's `IntentForwarder.dispatch` against the live app flips `fire_enabled`
(toggle-fire), toggles `state` Disabled↔Enabled (arm), and HOME — proving the key→HTTP→app chain.

---

## 8. Findings & gotchas (cost real time — don't rediscover)

- **Root needs `python3-yaml` + `python3-evdev` SYSTEM-WIDE.** PyYAML was only in jayson's pip `~/.local`,
  so the root supervisor's `load_config()` silently fell back to defaults (`enabled=False`) and the daemon
  exited "idle" — `config._load_yaml` swallows `ImportError`. `turret.service` works because it runs as jayson.
- **`config.yaml` had a STALE `remote:` block** (gpio_bcm 17, old KEY_POWER/KEY_OK names) overriding the new
  config.py defaults → the intent map would have matched nothing. Keep config.yaml's `remote:` in sync with config.py.
- **`systemctl stop turret.service` blocks ~5 s** (clean disarm + TimeoutStopSec). The supervisor dispatch is
  synchronous, so it's briefly unresponsive during a stop — **don't mash POWER** (queued presses each toggle).
- **Xorg + triggerhappy (thd) also read event0** → without the grab, remote digits leak into X as keystrokes.
  `grab=True` (EVIOCGRAB) makes the supervisor the exclusive reader; keep it on.
- **deploy.sh secrets check** must `sudo test -f /etc/alloy/secrets.env` (the dir is root-only; a bare `[ -f ]`
  runs as the deploy user and false-negatives).
- **Manual-steering ceiling (unchanged):** NEC repeats ~108 ms → ~8 jog events/s held; nudge + hold-to-slew is
  coarse, not proportional. Servos (MG996R ~315–400°/s) aren't the limit.

---

## 9. Hard-constraint compliance (satisfied)

- ✅ **Single servo mover** — the supervisor only POSTs intents / runs systemctl; the app's control thread
  alone moves servos.
- ✅ **No `time.sleep` in the control loop** — blocking I/O lives in the supervisor process; the app is untouched.
- ✅ **Best-effort** — every dispatch + the read loop is try/excepted.
- ✅ **Clamps preserved** — jog forwards to `/api/control-cmd`, which applies pan 5–47 / tilt 5–25.
- ✅ **Fire stays non-blocking** — FIRE → existing `/api/cmd fire_now`; honors fire-enable + cooldown.
- ✅ **Python 3.9** — no `match`/unions in the supervisor or config.
- ✅ **Pure `build_intent_map` + `http_plan`**, Mac unit-tested (`tests/test_remote_supervisor.py`, 28 tests);
  evdev lazy.
- ✅ **Run-block guarded** (`remote_daemon.py` / `main()` under `if __name__ == '__main__'`).
- ✅ **v1 untouched**; ships `remote.enabled=False`.
- ✅ **EVIOCGRAB** on (stops digit-leak to Xorg/tty).

---

## 10. Deferred / optional follow-ups

- **MANUAL-while-armed jog** — the original §9 in-process MANUAL state (jog while auto-aim is suspended) is
  **deferred**. Jog currently works only when DISARMED (the existing `/api/control-cmd` guard). Adding it
  needs in-app changes to `app/control.py` / `app/statemachine.py`, not the supervisor.
- **Strategy presets** on digits 1/2/3 — keys are mapped but unused (no web endpoint yet).
- **Reboot test** — both units are `enabled`; a reboot should auto-start them (overlay + keytable + supervisor).
- **On-rig tuning** — finalize the button map and jog step/accel/`-D`/`-P` to taste.
- **Dashboard** — re-import `monitoring/dashboards/pi-health.json` into Grafana for the new "Services"
  liveness row (`turret-remote.service` is already matched by Alloy's `unit_include "(turret.*|alloy)"`).

---

## 11. Files

`app/remote_supervisor.py` (pure `build_intent_map`/`http_plan`, `IntentForwarder`, `RemoteSupervisor` loop) ·
`remote_daemon.py` (entrypoint) · `config.py` + `config.yaml` (`RemoteConfig`) · `main.py` (in-process listener
removed) · `monitoring/systemd/{turret-remote,pi-turret-ir}.service` · `monitoring/ir-load-keytable.sh` ·
`monitoring/rc_keymaps/pi_turret.toml` · `monitoring/deploy.sh` · `monitoring/dashboards/generate_dashboards.py`
+ `pi-health.json` · `tests/test_remote_supervisor.py`. Pins: `README.md` "Wiring & hardware (as-built)" +
`IMPLEMENTATION_PLAN.md §8`.
