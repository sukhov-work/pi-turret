# mem:project/dev_environment

The three-machine model and what it means for verification. Read before any task touching hardware,
the model pipeline, or timing. (Referred by `mem:core`.)

## Machines & roles
| Machine | Role | Truth it holds |
|---|---|---|
| Mac M3 (ARM) | author + polish code, pure-logic pytest, git | none for hardware/TPU; cannot run `edgetpu_compiler` |
| Strix Halo, Ubuntu (x86-64) | train / export / INT8-quant / `edgetpu_compiler` | the only machine that compiles Edge-TPU models |
| Pi 4, Bullseye (`jayson@pi-jayson.local`) | deploy + on-device tests | the only source of camera/Coral/servo/FPS/aiming truth |

## Guiding principle
"Runs on the Mac" ≠ "verified" for anything touching the camera, Coral, servos, pump, or timing —
those are Pi-only facts. Pure logic (decode, NMS, calibration, controller, state machine) is Mac-truth.

## What CAN'T be tested locally (Mac)
- `picamera2` / `libcamera` / `RPi.GPIO` / `smbus` imports (hardware-only) — mock them in tests.
- Real inference latency / FPS, Coral execution, servo travel, aiming accuracy, fire timing.
- `edgetpu_compiler` (x86-64 only — the Strix box).

## Practical workflow
1. Author + unit-test logic on the Mac (hardware mocked, no import side effects).
2. Build/export/compile models on the Strix box; the compiled file ends `_edgetpu.tflite`.
3. `rsync` to the Pi; run on-device; record measured numbers; bench servos dry before any live fire.

## Parity tips
- Keep on-device code Python-3.9-clean (no `match`, no `X | Y` unions).
- Keep hardware behind interfaces (Detector / ServoController / Camera) so the Mac can inject fakes.
- Pin lib versions that matter for the Pi; verify ARM wheel availability before adopting a dep.
