# Hardware & Safety Conventions

This project aims and fires real actuators at live targets. These rules are **safety
invariants — violating them is a bug, not a style nit.** They mirror the non-negotiables
in `CLAUDE.md` and the skill; this file is the detailed reference.

Most of these facts are **Pi-only truth** — "it ran on the Mac" never verifies anything
here (see `project/dev_environment.md` and the three-machine model).

## Power & wiring invariants

| Rule | Why |
|------|-----|
| Servos on a **separate 5–6 V supply**, common ground with the Pi | Servo stall current browns out the Pi → resets / SD corruption |
| Pump/laser driven through a **relay or MOSFET with a flyback diode** | A bare GPIO can't source the load; inductive kickback destroys the pin |
| **Never** drive pump or servos from the Pi 5 V rail | Same brown-out / over-current risk |
| One common ground reference across Pi, driver board, supplies | Floating grounds = erratic PWM / phantom triggers |

## Wiring map (as-built — FIXED, do not rewire)

These pins are reused **exactly** from v1 (verified `v1/TurretHandler.py:40-51`, `v1/PCA9685.py:32`).
**Escalate before changing any breadboard / relay / diode / pin assignment** — the owner does not want
the rig rewired without a serious reason. New hardware is **additive on free pins only**, and even then
flag it for confirmation.

| Function | Bus / pin | Notes |
|---|---|---|
| PCA9685 servo driver | I2C bus 1 @ `0x40` | init once |
| 1602A LCD | I2C bus 1 (`rpi_lcd`, ~`0x27`) | shares the bus with the PCA9685 — no conflict |
| Pan / Tilt servo | PCA9685 ch **1** / ch **0** | |
| Water pump (v1 "main laser") | GPIO **BCM 26** | relay/MOSFET + flyback diode, never bare GPIO |
| Aux laser / aim marker | GPIO **BCM 24** (rewired from v1's BCM27) | opt-in (laser safety); `gpiozero.LED` |
| Status LED | GPIO **BCM 23** | `gpiozero.LED` |
| IR receiver (CONFIRMED) | GPIO **BCM 17** / pin 11 | additive; `dtoverlay=gpio-ir` |

Free pins besides BCM17: 4/5/6/12/13/16/18/19/20/21/22/25 + the SPI block (BCM27 freed by the aux rewire).
BCM 2/3 = I2C; **17 (IR)**/23/24 (aux)/26 in use.

**LCD + indicators are fail-safe outputs:** an I2C/GPIO error on the LCD or a status/aux indicator must
be logged and swallowed — a flaky display never stops the turret. Drive the status LED on while not
SAFE; keep the BCM24 aux laser marker OFF unless explicitly enabled in config.

## Actuator limits (clamp before every write)

| Axis | Channel (v1) | Clamp | Notes |
|------|--------------|-------|-------|
| Pan  | PCA9685 ch 1 | **5–47°** | |
| Tilt | PCA9685 ch 0 | **5–25°** | |
| Servo pulse | — | MG996R band **~1000–2000 µs** | clamp pulses too, not just angles |

- **Clamp at the boundary**, in `actuate`, on every command — never trust an upstream angle.
- **PCA9685 is initialized once** (MODE2 set at start); do **not** toggle MODE2 per move.
- `setPWM` already `int()`-coerces its args — the legacy float/`&` `TypeError` is **already
  fixed**. Don't "re-fix" it (the V2 design doc still lists it as a TODO; that note is stale).

## Fire control

- **Fire is non-blocking.** Pump/laser ON → timer → OFF → `COOLDOWN`, while tracking
  continues. **No `time.sleep` in the loop.** (v1 blocked ~5–8 s per engagement.)
- **Pump/laser OFF on every exit path** (`try/finally`) — including exceptions, shutdown,
  and disarm. This is the single most important runtime rule.
- A **cooldown** must elapse between shots; firing is gated by an explicit `ARMED` state.
- **Human interlock:** never fire when a person is detected. v1's person check ran at
  confidence `0.9` (effectively off) despite its own comment saying it must be ≤ `0.3` —
  v2 must make the interlock real and conservative (low threshold = err toward *not* firing).

## Failsafe & lifecycle

- **Disarm = the safe state:** relax/center servos, pump off, status LED indicates safe.
- Any `ServoError`/`PumpError`, any uncaught control-loop exception, and any shutdown
  signal must converge to **disarm** (see `error-handling.md`).
- Keep v1's `atexit` + `SIGTERM`/`SIGINT` → graceful-exit instinct; ensure it disarms.

## Detector correctness is a safety property

Aiming at garbage is unsafe. The detector is **anchor-free YOLOv8/YOLO11**: output
`[1, 4+nc, N]`, **no objectness channel** (single class = `[1, 5, N]`; `N = Σ(input/stride)²`, e.g.
**1344 @256**, 8400 @640). Decode = transpose → `boxes = out[:, :4]` (xywh) → `scores = out[:, 4:]` →
threshold → NMS → clip to frame. (For our edgetpu export, xywh are **normalized** → `coords_normalized=True`.)
**Never apply a YOLOv5 (anchor/objectness) decoder** — that was the v1 Coral accuracy bug.
Unit-test the decode against an Ultralytics `model.predict` reference (see `testing.md`).

## Coral / Edge-TPU rules

- `edgetpu_compiler` runs **only** on the Strix Halo x86-64 box — never on the Pi or Mac.
- The compiled file must be **the edgetpu artifact** (name contains `_edgetpu`). We load it via
  **pycoral `make_interpreter`**, which engages the TPU regardless of the exact filename; run-versioned
  names like `..._edgetpu_run<N>.tflite` are fine. (The stricter "*ends* `_edgetpu.tflite`" rule is an
  *Ultralytics-AutoBackend* concern — it silently falls back to CPU on a name mismatch — and we don't use
  AutoBackend on the Pi.)
- **I/O is INT8, verified on-device (run1):** input tensor is **int8** (scale `1/255`, zero `-128`) — the
  detector must **quantize the [0,255] frame per the input tensor's dtype/quant** (`coral.py._preprocess`
  does this); feeding raw uint8 raises `ValueError`. Output is **int8** dequantized to **normalized [0,1]**
  xywh → `decode_v8(..., coords_normalized=True)`. Output shape `[1,4+nc,N]`, N anchors `= Σ(input/stride)²`
  (1344 @256). Decode correctness is pinned by the golden fixture (`testing.md`).
- INT8 export needs **> 300 representative calibration images** matching deployment imagery.
- Stay on **`libedgetpu1-std`** unless active cooling is added (`-max` overheats unattended). Keep the
  Coral on a **USB3** port (≈3× faster than USB2).
- Full retrain/deploy loop: `claude-docs/MODEL_ITERATION.md`.

## On-device platform rules

- **Python 3.9 on the Pi** — no `match`, no `X | Y` unions, no 3.10+ syntax in on-device code.
- **picamera2 lores on Pi 4 must be YUV420** (RGB lores is Pi 5 only). Size lores to the
  model input to avoid a resize cost.
- Two cameras have distinct roles: **Pi Camera = detection** (not streamed), **USB webcam =
  stream** (not detection). Don't cross them.

## Bench protocol before any live test

1. Servos **dry-run within clamps with the pump/laser disconnected** first.
2. Verify disarm works (kill the process → servos relax, output off).
3. Confirm the human interlock blocks a fire on a person frame.
4. Only then a **decoy fire test** — measure hits, never claim aiming results not measured on the Pi.

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Write a raw angle/pulse to a servo | Clamp in `actuate` on every write |
| Bare GPIO for pump/laser | Relay/MOSFET + flyback diode |
| Servos on the Pi 5 V rail | Separate 5–6 V supply, common ground |
| `time.sleep` to time a shot | Non-blocking timer + state machine |
| Leave the interlock at a high threshold | Conservative human-detect, err toward not firing |
| Compile Edge-TPU model on Pi/Mac | Compile on the Strix Halo box; file ends `_edgetpu.tflite` |
| Trust a YOLOv5 decode on a v8 model | Anchor-free decode, unit-tested vs `model.predict` |
| Claim hardware/FPS/aiming verified from the Mac | Measure on the Pi; record the number |
