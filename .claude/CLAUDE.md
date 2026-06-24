# CLAUDE.md — pi-turret

Autonomous Raspberry-Pi pan/tilt **water-cannon bird deterrent**: Pi Camera → bird detection →
servo aim → pump fires. **v1 works and is the rollback.** We are building **v2 alongside it**,
never editing v1 in place. Hardware is fixed; Debian 11 Bullseye + Python 3.9 is retained.

## Read these before working
- **Use the `/turret` skill** for any implement / fix / design / research task — it carries the
  full workflow and constraint set. (`.claude/skills/turret/SKILL.md`)
- `.claude/claude-docs/V2-design-plan.md` — what to build (backend, Edge-TPU pipeline, aiming, phases).
- `.claude/claude-docs/pi-turret-v1-legacy-design.md` — what exists today (file map, servo math, GPIO map, footguns).
- `.claude/claude-docs/IMPLEMENTATION_PLAN.md` — the build plan. If missing, generate it first (see the skill).

## Three machines — do not confuse them
| Machine | Use for | Truth it holds |
|---|---|---|
| **Mac M3** (ARM) | author + polish code, run pure-logic `pytest`, git | none for hardware/TPU; **can't** run `edgetpu_compiler` |
| **Strix Halo, Ubuntu 25** (x86-64) | train / export / INT8-quant / `edgetpu_compiler` | the **only** machine that compiles Edge-TPU models |
| **Pi 4, Bullseye** (`jayson@pi-jayson.local`) | deploy + on-device tests | the **only** source of camera, Coral, servo, FPS, aiming truth |

"Runs on the Mac" ≠ "verified" for anything touching the camera, Coral, servos, pump, or timing.

## Non-negotiables (violations are bugs)
- Never modify v1 in place; v2 has its own dir + venv.
- Detector is **anchor-free YOLOv8/YOLO11**, output `[1,4+nc,8400]`, **no objectness** (single class
  `[1,5,8400]`). **Never apply a YOLOv5 decoder** — that was the v1 Coral accuracy bug.
- `edgetpu_compiler` runs **only** on the Strix Halo box. Compiled file **must end `_edgetpu.tflite`**.
- Fire is **non-blocking** (no `time.sleep` in the loop): pump on → timer → off → COOLDOWN, keep tracking.
- Pump via **relay/MOSFET + flyback diode**, never a bare GPIO. Servos on a **separate 5–6 V supply**, common ground.
- Keep clamps **pan 5–47°, tilt 5–25°**; MG996R pulse band ~1000–2000 µs. PCA9685: init once, no per-move MODE2 toggle.
- **Python 3.9 on the Pi** — no `match`, no `X | Y` unions in on-device code.
- picamera2 lores on Pi 4 must be **YUV420** (RGB lores is Pi 5 only). Stay on `libedgetpu1-std` unless actively cooled.

## Commands
```bash
# Run v1 (rollback reference) — on the Pi
python3 main.py                      # Bottle UI on :8001

# Tests (pure logic: decode, NMS, calibration, controller, state machine) — on the Mac
python -m pytest tests/ -v

# Deploy v2 to the Pi
rsync -av --exclude .git ./ jayson@pi-jayson.local:~/pi-turret-v2/
ssh jayson@pi-jayson.local

# Model build/export — on the Strix Halo box ONLY (verify current Ultralytics flags first)
yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=bird.yaml nms=False
```
The v2 venv/layout and exact test runner are set in Phase 0 of the plan — confirm before assuming.

## Repo layout
- **v1 (do not edit):** `main.py`, `TurretHandler.py`, `YOLOv8.py`, `PCA9685.py`, `Utils.py`,
  `models/`, `edgetpu-yolo/`, `mjpg-streamer/`, `index.html`.
- **v2 (build here):** new tree + venv per the plan; v1 stays runnable.

## Top footguns
- `TurretHandler.py` runs a full hardware-init + infinite detection loop **at import** (trailing
  unguarded module code). Guard all run blocks with `if __name__ == '__main__':`.
- Two cameras: **Pi Camera = detection** (not streamed), **USB webcam = stream** (not detection).
- The SSD person path uses a **hard-coded absolute model path**; v2 phase 1 drops it anyway.
- v1's gradual servo slew can take **~2.8 s** and blocks during fire — v2 replaces it with a
  closed loop and non-blocking fire.

After meaningful work, append a one-line entry to `.claude/claude-docs/DECISIONS.md` and update this file if a
constraint or workflow changed.