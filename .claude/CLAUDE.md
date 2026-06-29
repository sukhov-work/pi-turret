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
- `.claude/claude-docs/PARAMETERS.md` — every tunable explained (long form of the UI ⓘ tooltips / `PARAM_DOCS`).

## Conventions — check before writing v2 code
| Convention | Path |
|---|---|
| Architecture & concurrency (threaded, non-blocking, single-slot buffers) | `.claude/conventions/architecture.md` |
| Naming (units in names; don't copy v1 typos) | `.claude/conventions/naming.md` |
| Error handling (fail to a safe state on any actuator error) | `.claude/conventions/error-handling.md` |
| Hardware & safety (clamps, power, fire, interlock, Coral) | `.claude/conventions/hardware-safety.md` |
| Testing (Mac/Strix/Pi split; decode-vs-reference golden test) | `.claude/conventions/testing.md` |

## Knowledge search order
1. **Serena memories** (`list_memories` → `read_memory`) — prior decisions, gotchas, machine setup
2. `.claude/claude-docs/` — design (V2), as-built (v1), build plan (IMPLEMENTATION_PLAN)
3. `.claude/conventions/` — coding standards
4. Codebase — `Grep` / `Read` (Serena semantic nav when its backend is connected)
5. External — `WebSearch` for current library/version facts (Ultralytics / pycoral / picamera2 move fast; don't trust version-pinned commands in the docs)

## Three machines — do not confuse them
| Machine | Use for | Truth it holds |
|---|---|---|
| **Mac M3** (ARM) | author + polish code, run pure-logic `pytest`, git | none for hardware/TPU; **can't** run `edgetpu_compiler` |
| **Strix Halo, Ubuntu 25** (x86-64) | train / export / INT8-quant / `edgetpu_compiler` | the **only** machine that compiles Edge-TPU models |
| **Pi 4, Bullseye** (reach via `ssh pi`) | deploy + on-device tests | the **only** source of camera, Coral, servo, FPS, aiming truth |

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
# Run v1 (rollback reference) — on the Pi, from inside the v1/ folder
cd v1 && python3 main.py             # Bottle UI on :8001

# Tests (pure logic: decode, NMS, calibration, controller, state machine) — on the Mac
python -m pytest tests/ -v

# Deploy (Mac = source of truth): COMMIT, then push-to-deploy. The boxes only get code you've
# committed AND pushed — remember both. (Remotes: `git remote -v`; pi/strix push-to-checkout into ~/pi-turret.)
git push origin main  # GitHub hub (sukhov-work/pi-turret)
git push pi main      # Pi    — push-to-checkout into ~/pi-turret (reach: ssh pi)
git push strix main   # Strix — push-to-checkout into ~/pi-turret (reach: ssh strix)
# hosts/users/key/passwords in .claude/.env (gitignored — never commit). rsync/scp = big artifacts only.

# Model build/export — on the Strix Halo box ONLY, via `ssh strix` (verify current Ultralytics flags first)
yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=bird.yaml nms=False
```
The v2 venv/layout and exact test runner are set in Phase 0 of the plan — confirm before assuming.

## Repo layout
- **v1 (do not edit) — all under `v1/`:** `v1/main.py`, `v1/TurretHandler.py`, `v1/YOLOv8.py`,
  `v1/PCA9685.py`, `v1/Utils.py`, `v1/models/`, `v1/edgetpu-yolo/`, `v1/mjpg-streamer/`, `v1/index.html`.
  Run it with `cd v1 && python3 main.py` — v1's relative paths resolve against the `v1/` CWD.
- **v2 (build here):** new tree + venv per the plan; v1 stays runnable.
- **Config:** typed defaults ← `config.yaml` (committed base) ← `config.local.yaml` (**git-ignored, per-box overlay**, written by the UI **Save**; per-key delta). **All 14 sections are UI-tunable + persistable**; edits re-sync into live objects via `apply_config()` (servo/tracker/capture/detector) — restart-only fields persist + apply on reboot. Calibrated home/limits/aim coeffs/rotation live in the overlay and restore on boot.
- **`models/` (committed):** Edge-TPU detector models for tests + deploy — vanilla `yolov8n_coco80_256_int8_edgetpu.tflite` + finetuned `bird_yolov8n_256_int8_edgetpu_run<N>.tflite` (+ `.pt` sources). See `models/README.md`. Compiled on Strix; a deployable file must end `_edgetpu.tflite`.

## Top footguns
- `v1/TurretHandler.py` runs a full hardware-init + infinite detection loop **at import** (trailing
  unguarded module code). Guard all run blocks with `if __name__ == '__main__':`.
- Two cameras: **Pi Camera = detection** (not streamed), **USB webcam = stream** (not detection).
- The SSD person path uses a **hard-coded absolute model path**; v2 phase 1 drops it anyway.
- v1's gradual servo slew can take **~2.8 s** and blocks during fire — v2 replaces it with a
  closed loop and non-blocking fire.

## After meaningful work
- Append a one-line entry to `.claude/claude-docs/DECISIONS.md` (what was decided, files touched, any number measured on-device).
- Persist durable, non-obvious findings as a **Serena memory** (`write_memory`). Naming: `architecture/<component>`, `decisions/<topic>`, `patterns/<pattern>`, `bugs/<issue>`. Invariants and code over prose; one memory per logical unit.
- Update this file if a constraint or workflow changed.