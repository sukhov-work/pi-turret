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
`[1, 4+nc, 8400]`, **no objectness channel** (single class = `[1, 5, 8400]`). Decode =
transpose → `boxes = out[:, :4]` (xywh × input) → `scores = out[:, 4:]` → threshold → NMS.
**Never apply a YOLOv5 (anchor/objectness) decoder** — that was the v1 Coral accuracy bug.
Unit-test the decode against an Ultralytics `model.predict` reference (see `testing.md`).

## Coral / Edge-TPU rules

- `edgetpu_compiler` runs **only** on the Strix Halo x86-64 box — never on the Pi or Mac.
- The compiled file **must end `_edgetpu.tflite`**, or the runtime silently falls back to
  CPU and the Coral is bypassed.
- INT8 export needs **> 300 representative calibration images** matching deployment imagery.
- Stay on **`libedgetpu1-std`** unless active cooling is added (`-max` overheats unattended).

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
