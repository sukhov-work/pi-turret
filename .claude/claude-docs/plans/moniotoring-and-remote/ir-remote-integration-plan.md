# pi-turret v2 — Step 1.15 IR Remote Control — Implementation Plan (agent handoff)

> **Placement:** `.claude/claude-docs/plans/moniotoring-and-remote/ir-remote-integration-plan.md` (alongside the original `migrate-to-allow.md` draft).
> **Status:** ready to build. Expands `IMPLEMENTATION_PLAN.md` **Step 1.15** from *SEAM ONLY* to a full buildable step. Phase-1 stretch feature (process control = must-have; manual steering = stretch).
> **Source-of-truth split:** this doc = *what to build*. `pi-turret-v1-asbuilt.md` = *what exists* (wiring/GPIO, §3/§13). The research report *"Integrating a 21-Key NEC IR Remote on Raspberry Pi 4 / Bullseye"* = *why* (full decode-stack comparison, exhaustive scancode table, NEC timing derivations) — this plan carries only the buildable essentials and points there for rationale.
> **Conventions:** every step lists goal · files · machine · validation · rollback. Machines per the three-machine model: **Mac** (author + pure-logic pytest), **Strix** (x86-64 model/compile — irrelevant here), **Pi** (`jayson@pi-jayson.local`, the only hardware truth). Confidence labels: **[VERIFIED]** primary/2-source · **[INFERRED]** · **[UNVERIFIED]** · **[ASSUMPTION]**.

---

## 0. What changed this revision (hardware correction)

The intermediate **IR receiver breakout PCB is lost** (the "遥控模块 / remote-control module" board: VCC·GND·OUT pads, a 3-pin header, a red indicator LED). It is **not required**. The bare 3-pin **VS1838B** still in hand *is* the receiver and connects directly to the Pi. The only thing worth replacing is the breakout's **on-board RC supply filter** (two passives, §1.2). Pin identity is **confirmed**: the overlay's `gpio_pin=17` means **BCM 17 = GPIO 17 = physical pin 11** (not physical pin 17, which is a 3V3 rail). **[VERIFIED]** Bare-device **pin order is locked by the owner** to the standard VS1838B front-view order **SIGNAL–GND–VCC** (lens facing you, legs down); see §1.3. **[ASSUMPTION — owner-provided, matches datasheet]**

No change to: decode stack, scancode-capture procedure, `RemoteConfig`/intent-slot/state-machine design, button map, or the manual-steering feasibility verdict.

---

## 1. Hardware (REVISED — bare VS1838B, no breakout)

### 1.1 What the lost board did vs the bare part
The breakout = VS1838B + **RC supply filter** ("On-board RC filter, work more stable" per the WCON module description) + a signal/power **indicator LED** + a male **header**. The LED and header are convenience only. The RC filter is the one electrically meaningful loss for an unattended outdoor unit (the VS1838B's internal AGC is supply-noise sensitive). **[VERIFIED]**

### 1.2 Wiring (bare device + replacement RC filter)
Add, right at the device legs:
- **100 Ω in series** with VCC (forms a low-pass with the cap; also limits fault current during pin-verification), and
- **0.1 µF ceramic** between the device VCC pin and GND, ideally **plus a 4.7–10 µF** electrolytic/ceramic in parallel for bulk.

This reproduces the datasheet application circuit ("a 100 Ω resistor in series with VCC and a 4.7 μF capacitor between VCC and GND") and the breakout's filter. Skipping it risks false triggers / reduced range under a noisy supply or strong ambient IR (sunlight). **[VERIFIED]**

**Pin order (locked, owner-confirmed):** front view — lens facing you, legs pointing down — **left = SIGNAL/OUT, middle = GND, right = VCC**. This is the standard VS1838B datasheet order. (From the **back/flat** side the order mirrors to VCC–GND–SIGNAL — don't wire from that side by mistake.)

| Bare VS1838B leg | Connects to | Pi pin | Notes |
|---|---|---|---|
| **OUT** | GPIO 17 | **pin 11** (BCM 17) | active-low demodulated signal; 0–3.3 V, MCU-safe |
| **GND** | Ground | pin 6/9/14/… | common ground |
| **VCC** | 3V3 (via 100 Ω) | pin 1 or 17 | power at 3.3 V; **do not** feed 5 V to a part whose OUT then drives the Pi |

- **3.3 V is in spec** (VS1838B working voltage 2.7–5.5 V) and the output swings 0–3.3 V → safe for Pi GPIO; no level shifter. **[VERIFIED]**
- **No external pull-up needed:** the `gpio-ir` overlay enables the internal pull-up (`gpio_pull=up` default) and the VS1838B output idles HIGH. If reception is flaky on this clone, a 10 kΩ OUT→3V3 pull-up is the documented fallback. **[VERIFIED]**
- Optional 100 Ω–1 kΩ in the **OUT** line as a low-pass if you later run a long lead to the receiver (>1 m). **[INFERRED]**

### 1.3 Pinout (LOCKED — owner-confirmed)
**Order: SIGNAL/OUT · GND · VCC**, left→right when viewing the **front** (domed lens toward you, legs down). Matches the standard VS1838B datasheet pinout. **[ASSUMPTION — owner-provided]**

Wire per §1.2. One sanity check on first power (because reversed VCC/GND destroys the part silently): with the 100 Ω series R in VCC limiting fault current, confirm **OUT idles HIGH (~3.3 V)** and dips LOW on a remote press before trusting it; then `ir-keytable -t` (§4) decoding clean NEC confirms wiring + protocol end-to-end. If OUT sits at 0 V or the part warms up, kill power and re-check orientation (front vs back mirror).

---

## 2. Decode stack — LOCKED: `gpio-ir` + rc-core + `ir-keytable` + python-evdev

Kernel decodes NEC in-ISR; the remote appears as `/dev/input/eventN`; Python reads `KEY_*` events via evdev. **No daemon, negligible CPU, and it is exactly the `app/remote.py` evdev seam** already planned. LIRC's GPIO path is deprecated on mainline; pigpio needs root for DMA. Full comparison table in the research report. **Confidence: [VERIFIED]** (Raspberry Pi `boot/overlays/README`; rc-core/ir-keytable docs).

---

## 3. Bring-up runbook (Pi) — exact commands

```bash
# 3.1 Overlay — Bullseye uses /boot/config.txt (NOT /boot/firmware/config.txt; that's Bookworm) [VERIFIED]
echo 'dtoverlay=gpio-ir,gpio_pin=17' | sudo tee -a /boot/config.txt
sudo reboot

# 3.2 Find the receiver — the rc index is NOT stable (vc4-hdmi CEC also registers as an rc device).
#     ALWAYS resolve by driver name 'gpio_ir_recv', never assume rc0/event0. [VERIFIED]
ir-keytable                      # look for: Driver: gpio_ir_recv ... /dev/input/eventN, /dev/lircN
for d in /sys/class/rc/rc*; do printf '%s -> ' "$d"; \
  cat "$d"/input*/name 2>/dev/null; done   # crude name->index resolver

# 3.3 Enable NEC + raw/decoded test (default keymap is rc6-mce → your NEC decodes to NOTHING until enabled) [VERIFIED]
sudo ir-keytable -s rcN -c -p nec -t        # press buttons; expect: protocol(nec): scancode = 0x..
# if nothing decodes, discover the real variant:
sudo ir-keytable -s rcN -c -p all -t
# raw pulse check (sanity that the diode + wiring work at all):
ir-ctl -r -d /dev/lircN

# 3.6 Permissions — reading /dev/input/eventN requires the 'input' group (nodes are root:input 0660).
#     The 'pi'/'jayson' user is typically already in 'input'. Otherwise: [VERIFIED]
sudo usermod -aG input "$USER"      # re-login to take effect
```

Keytable + auto-load (systemd oneshot — preferred over the flaky udev `rc_maps.cfg` path):
```toml
# /etc/rc_keymaps/pi_turret.toml   (see §4 for the full button set; VERIFY scancodes per unit)
[[protocols]]
name = "pi_turret"
protocol = "nec"
variant = "nec"
[protocols.scancodes]
0x45 = "KEY_STOP"          # CH-   -> ESTOP
0x47 = "KEY_CHANNELUP"     # CH+   -> ARM toggle
0x46 = "KEY_HOMEPAGE"      # CH    -> HOME / center
0x44 = "KEY_PREVIOUS"      # |<<   -> JOG_PAN-
0x40 = "KEY_NEXT"          # >>|   -> JOG_PAN+
0x43 = "KEY_PLAYPAUSE"     # >||   -> FIRE
0x07 = "KEY_VOLUMEDOWN"    # -     -> JOG_TILT-
0x15 = "KEY_VOLUMEUP"      # +     -> JOG_TILT+
0x09 = "KEY_MODE"          # EQ    -> TOGGLE_FIRE_ENABLE
0x16 = "KEY_NUMERIC_0"     # 0     -> MANUAL toggle
0x0c = "KEY_NUMERIC_1"
0x18 = "KEY_NUMERIC_2"
0x5e = "KEY_NUMERIC_3"
```
```ini
# /etc/systemd/system/pi-turret-ir.service
# -D/-P set the autorepeat delay/period low so hold-to-slew starts fast (see §11).
# NOTE: -P changes are unreliable on some drivers and may not persist — verify with `ir-keytable` after boot. [VERIFIED]
[Unit]
Description=Load pi-turret IR keytable
After=multi-user.target
[Service]
Type=oneshot
# If rc index drifts, replace `-a` auto with a name-resolver wrapper that passes `-s rcN`.
ExecStart=/usr/bin/ir-keytable -a /etc/rc_keymaps/pi_turret.toml -D 150 -P 110
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now pi-turret-ir.service
```

---

## 4. Scancode reference (compact) + capture

rc-core reports the **LSB-first NEC command byte** (the bit-reversal of the Arduino-IRremote MSB byte); this clone's NEC address is `0x00`, so `ir-keytable -t` prints the bare command byte (`0x45`-style). **The number keys are exact/verified; the prev/next/play labels can differ between print-variants — capture on YOUR unit.** **[VERIFIED for digits; UNVERIFIED per-unit for transport keys]**

| Button | rc-core `nec` scancode | Button | scancode | Button | scancode |
|---|---|---|---|---|---|
| CH− | 0x45 | − | 0x07 | 3 | 0x5e |
| CH | 0x46 | + | 0x15 | 4 | 0x08 |
| CH+ | 0x47 | EQ | 0x09 | 5 | 0x1c |
| \|<< | 0x44 | 0 | 0x16 | 6 | 0x5a |
| >>\| | 0x40 | 100+ | 0x19 | 7 | 0x42 |
| >\|\| | 0x43 | 200+ | 0x0d | 8 | 0x52 |
| 1 | 0x0c | 2 | 0x18 | 9 | 0x4a |

Capture: `sudo ir-keytable -s rcN -c -p nec -t`, press each physical button, record the `scancode =` line, write into the `.toml`. NEC repeat frames are consumed by the kernel as autorepeat (no `0xFFFFFFFF` surfaces). **[VERIFIED]**

---

## 5. Build steps (sub-steps of 1.15)

| # | Step | Goal | Files | Machine | Validation | Rollback |
|---|---|---|---|---|---|---|
| 1.15.0 | Hardware | Bare VS1838B + RC filter wired; pinout verified | — (bench) | **Pi** | OUT idles HIGH, dips on press; `ir-ctl -r` shows pulses | unplug; feature off |
| 1.15.1 | OS/keytable | Overlay + NEC keytable auto-loaded; perms | `/boot/config.txt`, `/etc/rc_keymaps/pi_turret.toml`, systemd unit, udev/group | **Pi** | `ir-keytable -t` decodes every mapped button; device resolvable by name | remove overlay line + service |
| 1.15.2 | `RemoteConfig` | Typed config (Py3.9, no unions) incl. key→intent map, jog params, toggle keys | `config.py`, `config.yaml` | **Mac** | loads, validates, round-trips; override via `config.local.yaml` | drop block |
| 1.15.3 | Listener + actions | `RemoteActions` ABC, **pure** `build_key_map`, `RemoteListener` evdev daemon (lazy evdev import) | `app/remote.py` | **Mac** (logic) + **Pi** (live) | `build_key_map` unit-tested on Mac; on-Pi key-down dispatches intent without touching servos | disable `remote.enabled` |
| 1.15.4 | Command slots | Lock-protected one-shot slot (seq, exactly-once) + jog accumulator (read-and-zero) | `app/pipeline.py` (shared state), `main.py` (`TurretRemoteActions`) | **Mac** + **Pi** | unit-test seq exactly-once + jog coalesce; control loop drains each tick | n/a |
| 1.15.5 | MANUAL state | Add MANUAL override to SM; suspends auto-aim, consumes jog deltas through same clamps + one-dir approach | `app/statemachine.py`, `app/control.py` | **Mac** (SM) + **Pi** (dry-run) | SM transition tests; on-Pi jog stays within pan 5–47 / tilt 5–25, single mover, no `time.sleep` | force `manual=False` |
| 1.15.6 | Manual fire | FIRE intent → existing non-blocking pump SM; honors fire-enable + cooldown | `app/control.py`, `actuate/pump.py` (reused) | **Pi** (LED stand-in → pump) | fires only under full predicate + fire-enable; would-fire mode logs only | would-fire/telemetry-only |
| 1.15.7 | Map + tune | Finalize button map; tune jog step/accel/timeout on-rig | `config.yaml`/`config.local.yaml` | **Pi** | hold-to-slew usable; e-stop unmistakable; digits = presets | revert to tap-only nudge |

---

## 6. `RemoteConfig` (drop-in, Python 3.9 — no `match`, no `X | Y`)

```python
# config.py
from dataclasses import dataclass, field
from typing import Dict, Optional

@dataclass
class RemoteConfig:
    enabled: bool = False                 # opt-in; SAFE default
    device_name: str = "gpio_ir_recv"     # match by NAME, not eventN (index drifts)
    device_path: Optional[str] = None     # optional explicit /dev/input/by-path/...
    grab: bool = True                     # EVIOCGRAB on headless: stop keystroke leak to tty
    key_map: Dict[str, str] = field(default_factory=lambda: {
        "KEY_STOP":       "ESTOP",                # CH-  (0x45) -- unmistakable, one-shot
        "KEY_CHANNELUP":  "ARM_TOGGLE",           # CH+  (0x47)
        "KEY_HOMEPAGE":   "HOME",                 # CH   (0x46)
        "KEY_MODE":       "TOGGLE_FIRE_ENABLE",   # EQ   (0x09)
        "KEY_PLAYPAUSE":  "FIRE",                 # >||  (0x43)
        "KEY_PREVIOUS":   "JOG_PAN_NEG",          # |<<  (0x44)
        "KEY_NEXT":       "JOG_PAN_POS",          # >>|  (0x40)
        "KEY_VOLUMEUP":   "JOG_TILT_POS",         # +    (0x15)
        "KEY_VOLUMEDOWN": "JOG_TILT_NEG",         # -    (0x07)
        "KEY_NUMERIC_0":  "MANUAL_TOGGLE",        # 0    (0x16)
        "KEY_NUMERIC_1":  "STRATEGY_1",
        "KEY_NUMERIC_2":  "STRATEGY_2",
        "KEY_NUMERIC_3":  "STRATEGY_3",
    })
    jog_step_deg: float = 2.0             # per key event; see §11 for the math
    jog_accel_after_repeats: int = 5      # consecutive autorepeats (value==2) before accel
    jog_accel_max_step_deg: float = 4.0
    repeat_delay_ms: int = 150            # ir-keytable -D (fast slew onset)
    repeat_period_ms: int = 110           # ir-keytable -P (near the ~108 ms NEC floor)
    manual_timeout_s: float = 8.0         # auto-exit MANUAL on inactivity
    fire_cooldown_s: float = 1.5
    oneshot_ignore_autorepeat: bool = True  # one-shots act only on value==1
```

---

## 7. `app/remote.py` skeleton (lazy evdev import; `build_key_map` stays pure & Mac-testable)

```python
# app/remote.py
import threading, time, select

class RemoteActions:                       # the seam other code implements
    def arm_toggle(self): raise NotImplementedError
    def toggle_fire_enable(self): raise NotImplementedError
    def estop(self): raise NotImplementedError
    def home(self): raise NotImplementedError
    def manual_toggle(self): raise NotImplementedError
    def fire(self): raise NotImplementedError
    def jog(self, axis, delta_deg): raise NotImplementedError
    def select_strategy(self, idx): raise NotImplementedError

def build_key_map(cfg):                    # PURE: KEY-name str -> intent str (unit-tested on Mac)
    return dict(cfg.key_map)

def _find_device(cfg):                     # evdev imported lazily so this module imports on Mac
    import evdev
    if cfg.device_path:
        return evdev.InputDevice(cfg.device_path)
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == cfg.device_name:
            return dev
        dev.close()
    return None

class RemoteListener(threading.Thread):
    def __init__(self, cfg, actions):
        super().__init__(daemon=True)
        self.cfg = cfg; self.actions = actions
        self.keymap = build_key_map(cfg)
        self._stop = threading.Event(); self._dev = None
        self._held = {}                    # keycode -> consecutive value==2 count

    def run(self):
        from evdev import ecodes
        while not self._stop.is_set():
            try:
                if self._dev is None:
                    self._dev = _find_device(self.cfg)
                    if self._dev is None:
                        time.sleep(1.0); continue
                    if self.cfg.grab:
                        try: self._dev.grab()
                        except Exception: pass
                r, _, _ = select.select([self._dev.fd], [], [], 0.5)
                if not r: continue
                for ev in self._dev.read():
                    if ev.type == ecodes.EV_KEY:
                        self._handle(ev, ecodes)
            except OSError:                # device vanished (replug / eventN renumber)
                try: self._dev.close()
                except Exception: pass
                self._dev = None; time.sleep(0.5)
            except Exception:
                time.sleep(0.2)            # best-effort: never propagate

    def _handle(self, ev, ecodes):
        name = ecodes.KEY.get(ev.code)
        if isinstance(name, list): name = name[0]
        intent = self.keymap.get(name)
        if intent is None: return
        if ev.value == 0:                  # key up
            self._held.pop(ev.code, None); return
        is_fresh = (ev.value == 1)
        try:
            if intent.startswith("JOG_"):
                n = self._held.get(ev.code, 0) + (0 if is_fresh else 1)
                self._held[ev.code] = n
                step = self.cfg.jog_step_deg
                if n >= self.cfg.jog_accel_after_repeats:
                    step = self.cfg.jog_accel_max_step_deg
                if   intent == "JOG_PAN_POS":  self.actions.jog("pan",  +step)
                elif intent == "JOG_PAN_NEG":  self.actions.jog("pan",  -step)
                elif intent == "JOG_TILT_POS": self.actions.jog("tilt", +step)
                elif intent == "JOG_TILT_NEG": self.actions.jog("tilt", -step)
            else:                          # one-shots: ignore autorepeat
                if self.cfg.oneshot_ignore_autorepeat and not is_fresh: return
                if   intent == "ESTOP":              self.actions.estop()
                elif intent == "ARM_TOGGLE":         self.actions.arm_toggle()
                elif intent == "TOGGLE_FIRE_ENABLE": self.actions.toggle_fire_enable()
                elif intent == "HOME":               self.actions.home()
                elif intent == "MANUAL_TOGGLE":      self.actions.manual_toggle()
                elif intent == "FIRE":               self.actions.fire()
                elif intent.startswith("STRATEGY_"):
                    self.actions.select_strategy(int(intent.split("_")[1]))
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        try:
            if self._dev and self.cfg.grab: self._dev.ungrab()
        except Exception: pass
```

---

## 8. `main.py` — `TurretRemoteActions` publishes into shared slots (never moves servos)

```python
class TurretRemoteActions(RemoteActions):
    def __init__(self, shared):            # shared = lock-protected slots in app/pipeline.py
        self.s = shared
    def _post(self, intent):
        with self.s.cmd_lock:
            self.s.cmd_seq += 1
            self.s.cmd = (self.s.cmd_seq, intent)
    def arm_toggle(self):         self._post("ARM_TOGGLE")
    def estop(self):              self._post("ESTOP")
    def home(self):               self._post("HOME")
    def toggle_fire_enable(self): self._post("TOGGLE_FIRE_ENABLE")
    def manual_toggle(self):      self._post("MANUAL_TOGGLE")
    def fire(self):               self._post("FIRE")
    def select_strategy(self, i): self._post("STRATEGY_%d" % i)
    def jog(self, axis, delta):
        with self.s.jog_lock:
            if axis == "pan": self.s.jog_pan += delta
            else:             self.s.jog_tilt += delta
```

---

## 9. Control-thread consumption + MANUAL state (the single servo mover; no blocking)

```python
def control_tick(self):
    # (1) drain one-shot command, exactly-once via monotonic seq
    with self.s.cmd_lock:
        cmd = self.s.cmd
    if cmd is not None and cmd[0] != self.last_cmd_seq:
        self.last_cmd_seq = cmd[0]
        self.apply_command(cmd[1])        # ESTOP/ARM/HOME/TOGGLE_FIRE_ENABLE/MANUAL/STRATEGY

    # (2) MANUAL: consume jog deltas through the SAME clamps + one-directional approach
    if self.manual:
        with self.s.jog_lock:
            dp, dt = self.s.jog_pan, self.s.jog_tilt
            self.s.jog_pan = 0.0; self.s.jog_tilt = 0.0
        if dp or dt:
            self.last_manual_activity = now()
            self.pan_target  = clamp(self.pan_target  + dp, 5, 47)
            self.tilt_target = clamp(self.tilt_target + dt, 5, 25)
        if now() - self.last_manual_activity > self.cfg.remote.manual_timeout_s:
            self.manual = False; self.state = "SEARCHING"

    # (3) servo P-step toward target (unchanged; one-directional final approach for backlash)
    self.step_servos_toward_targets()
    # (4) non-blocking fire SM (unchanged; honors fire-enable + cooldown)
    self.service_fire_state_machine()
```

`apply_command` rules:
- **ESTOP** (any state, idempotent, best-effort): `armed=False` → pump OFF (de-energize BCM26) → optional HOME → COOLDOWN/SEARCHING. Wrapped so a remote fault can't crash control.
- **MANUAL_TOGGLE**: flip `self.manual`; on enter, suspend auto-aim and seed `pan_target/tilt_target` from current angles; on exit → SEARCHING.
- **FIRE**: in MANUAL, set a manual-fire request the fire SM consumes; still gated by fire-enable + (kill-zone bypass allowed in manual) + cooldown. In would-fire mode it only logs.
- **ARM_TOGGLE / TOGGLE_FIRE_ENABLE / HOME / STRATEGY_n**: flip the corresponding flag / set target / select preset.

---

## 10. Button map (this 21-key remote)

| Button | Intent | Why |
|---|---|---|
| **CH−** | ESTOP | isolated, findable by feel; one-shot only |
| CH+ | ARM / DISARM | pairs with e-stop |
| CH | HOME / center | |
| EQ | TOGGLE_FIRE_ENABLE (would-fire ↔ live) | distinct, central |
| **>\|\|** | FIRE | natural trigger |
| \|<< / >>\| | JOG_PAN − / + | left/right d-pad, comfortable to hold |
| − / + | JOG_TILT − / + | down/up |
| 0 | ENTER/EXIT MANUAL | gates jog + manual-fire |
| 1 / 2 / 3 | STRATEGY presets | digits = presets |
| 4–9, 100+, 200+ | spare (annotation cycle / absolute-angle jumps) | |

---

## 11. Manual-steering feasibility verdict (quantified)

- **Ceiling:** NEC repeat frames arrive every **~108 ms**; the kernel re-emits held keys at its default **125 ms** period (`Repeat delay 500 / period 125`, settable via `-D/-P` but floored by the IR cadence) → **~8 jog events/s** held. **[VERIFIED]**
- **Envelope:** pan 42°, tilt 20°. At **2°/step**: full pan ≈ 21 steps ≈ **2.4–2.6 s** held; tilt ≈ 10 steps ≈ **1.1–1.25 s**. At 1°/step, double those (finer aim, rely on taps for final approach).
- **Servos are not the limit:** MG996R ≈ 0.15–0.19 s/60° (≈315–400°/s); a 1–2° step finishes in <10 ms, far inside the 108 ms IR window. **[VERIFIED]**
- **Scheme:** **nudge + hold-to-slew** — each fresh press = one step; holding slews at ~8 steps/s; optional acceleration after ~5 held repeats (step 2°→4°) to cross the envelope; `-D 150` so slew starts fast. **This is coarse, not proportional joystick control** — matches the accepted "singular commands are fine" worst case. **[VERIFIED]**

---

## 12. Hard-constraint compliance checklist (the agent must satisfy all)

- [ ] **Single servo mover:** `RemoteListener` never touches PCA9685/pump; only publishes intents. Servos move solely in `control_tick`.
- [ ] **No `time.sleep` in the control loop.** Blocking reads live only in the listener's own thread (`select`/`read_loop`).
- [ ] **Best-effort remote:** every dispatch + the listener loop is try/excepted; a remote fault never crashes control.
- [ ] **Clamps preserved:** pan 5–47°, tilt 5–25° applied to manual jog identically.
- [ ] **Fire stays non-blocking** and honors fire-enable + cooldown, including manual fire.
- [ ] **Python 3.9 on-device:** no `match`, no `X | Y` unions in `app/remote.py`, `config.py`, control code.
- [ ] **`build_key_map` is pure** and unit-tested on Mac; evdev import is lazy so the module imports without hardware.
- [ ] **Module-level run guarded** by `if __name__ == '__main__':` (no v1-style import-time execution).
- [ ] **v1 untouched;** feature ships `enabled=False` by default.
- [ ] **EVIOCGRAB** on headless so digits don't leak to a tty.

---

## 13. Repo bookkeeping to land with the change

**(a) `IMPLEMENTATION_PLAN.md` — replace the Step 1.15 row:**
> | 1.15 IR remote (`app/remote.py` + `RemoteConfig`) | **PLANNED → buildable** (pin GATE cleared: BCM17/GPIO17/pin11; stack = gpio-ir+rc-core+evdev; bare VS1838B + RC filter, breakout lost) | OS/keytable + listener + MANUAL state + manual fire on Pi; tune jog on rig. Plan: `claude-docs/plans/moniotoring-and-remote/ir-remote-integration-plan.md` |

Also flip Step 1.15's status note from "SEAM ONLY" and update **§7 Open questions**: the IR receiver pin is resolved (GPIO17/pin11); remaining IR open items are on-Pi (actual scancodes, rc index, measured held cadence, EVIOCGRAB sufficiency, `-D/-P` persistence).

**(b) `docs/DECISIONS.md` — append:**
> **2026-06-29 — IR remote (Step 1.15).** Stack: `gpio-ir` overlay + rc-core + `ir-keytable` + python-evdev (daemon-free, in-kernel NEC decode, fits the evdev seam; LIRC/pigpio rejected). Receiver: **bare VS1838B on GPIO17 (pin 11)** at 3.3 V — the breakout PCB is lost, so add a replacement RC supply filter (100 Ω series in VCC + 0.1 µF, +4.7–10 µF bulk). Pin order locked (owner) to the standard VS1838B front-view order **SIGNAL–GND–VCC** (legs down); sanity-check only on first power that OUT idles high (reversed VCC/GND kills it). Manual steering capped at ~8 steps/s by the NEC ~108 ms repeat → nudge + hold-to-slew, not proportional. Remote publishes intents into lock-protected slots; control thread stays the single servo mover; new MANUAL state; manual fire honors fire-enable + cooldown. Files: `app/remote.py`, `RemoteConfig` in `config.py`, `TurretRemoteActions` in `main.py`, slots in `app/pipeline.py`, MANUAL in `app/statemachine.py`/`app/control.py`.

**(c) `claude-docs/PARAMETERS.md` — add the `RemoteConfig` params** (`remote.enabled`, `device_name`, `device_path`, `grab`, `key_map`, `jog_step_deg`, `jog_accel_after_repeats`, `jog_accel_max_step_deg`, `repeat_delay_ms`, `repeat_period_ms`, `manual_timeout_s`, `fire_cooldown_s`, `oneshot_ignore_autorepeat`) with the one-line descriptions from §6, so they appear in the web-UI per-param ⓘ docs.

**(d) Wiring map (`IMPLEMENTATION_PLAN.md` §8)** already lists "IR receiver (PROPOSED) — BCM 17". Change PROPOSED → **confirmed**, add the note "bare VS1838B + RC filter; breakout lost".

---

## 14. On-Pi gates / open questions (owner)

1. **Actual scancodes + protocol variant** of *this* remote (capture §3.3/§4 — transport-key labels vary per print run). **[UNVERIFIED per-unit]**
2. ✅ **Bare-device pin order — RESOLVED (owner):** SIGNAL · GND · VCC, front view (lens toward you, legs down) — matches the standard VS1838B datasheet order. Sanity-check only: OUT idles HIGH on first power. **[ASSUMPTION — owner-provided]**
3. **Which rc index** the receiver lands on; whether a name→`-s rcN` resolver is needed in the unit. **[INFERRED it varies]**
4. **Measured held cadence** (events/s) → confirms the 2°/step + accel choice. **[VERIFIED ceiling; per-rig TBD]**
5. **EVIOCGRAB** needed/sufficient to stop console leakage in the headless config. **[INFERRED yes]**
6. **`-D/-P` persistence** across boot on this kernel (re-apply in the oneshot if not). **[VERIFIED unreliable on some drivers]**
7. **Receiver placement/range** (5–8 m, ±45°) clear of the aux laser/status LED and direct sun. **[VERIFIED spec; siting TBD]**
