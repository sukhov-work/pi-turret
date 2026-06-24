---
name: turret
description: >
  Development skill for the pi-turret v2 project — a Raspberry Pi 4 + Coral USB autonomous
  pan/tilt water-cannon bird deterrent (Python 3.9, Bullseye). Builds and maintains the v2
  implementation plan from the design docs, then implements, fixes, designs, and researches
  against it. Use this skill for ANY pi-turret work: "implement", "build", "add", "fix",
  "debug", "design", "plan", "investigate", "research", any Phase/Step reference
  (e.g. "Phase 1", "the NCNN backend step"), or any task touching detection, the Edge-TPU
  model pipeline, decode/NMS, tracking, aiming/calibration, servos/PCA9685, the pump,
  picamera2, the Bottle/stream layer, or the on-device environment. Trigger it even when the
  user names a component without saying "skill" (e.g. "the decode is wrong", "wire up the
  pump", "make it track"). It is parallel-first and confidence-tracked, and it knows the
  three-machine workflow (Mac authoring, x86-64 compile box, Pi-only hardware truth).
argument-hint: <what to build, fix, investigate, or design>
usage: /turret <task description>
examples:
  - /turret build the v2 implementation plan from the design docs
  - /turret implement Phase 1 — NCNN single-class bird detector with correct v8 decode
  - /turret fix the Edge-TPU model: boxes are garbage
  - /turret design the closed-loop pixel-error -> servo controller
  - /turret research current Ultralytics NCNN export flags for Pi 4
  - /turret wire the water pump fire path as a non-blocking state machine
---

# pi-turret v2 Dev Skill

You are implementing, designing, fixing, and researching the **pi-turret v2** rebuild: an
autonomous pan/tilt water-cannon that detects birds and squirts them. Hardware is fixed;
Debian 11 Bullseye + Python 3.9 is retained; **v2 is built alongside the working v1, which is
never modified in place.**

**Canonical references — read the relevant parts before acting:**
- `.claude/claude-docs/V2-design-plan.md` — the v2 design (backend choice, Edge-TPU pipeline, aiming,
  concurrency, migration phases, risk matrix). The source of truth for *what to build*.
- `.claude/claude-docs/pi-turret-v1-legacy-design.md` — the legacy system as-built (file map, data flow, servo math,
  GPIO/I2C map, magic numbers, footguns). The source of truth for *what exists today*.
- `.claude/claude-docs/IMPLEMENTATION_PLAN.md` — the step-by-step build plan. If it does not exist yet, your
  first job on any Implement request is to generate it (see Design/Plan below).

If these files live elsewhere, locate them with Glob before assuming they are missing.

## The three-machine model (do not confuse them)

| Machine | Role | What is true here |
|---|---|---|
| **Mac M3** (ARM) | Author + polish code, run pure-logic unit tests, git | No camera/TPU/servo truth. Cannot run `edgetpu_compiler` (ARM). |
| **Strix Halo, Ubuntu 25** (x86-64) | Train / finetune / export / INT8-quantize / `edgetpu_compiler` | The **only** place the Edge-TPU compiler runs. Do all model builds here, native or Docker. |
| **Raspberry Pi 4**, Bullseye, `jayson@pi-jayson.local` | Deploy + on-device tests | The **only** source of hardware, inference, FPS/latency, and aiming truth. Confirm the host before pushing. |

"Runs on the Mac" never means "verified" for anything involving the camera, the Coral, servos,
the pump, or real timing. Those are Pi-only facts.

## Workflow

```
Phase 0: Classify + load context
Phase 1: Parallel research (only when the docs don't already answer it)
Phase 2: Execute (implement / fix / design / research)
Phase 3: Verify (split: Mac-runnable vs Pi-only)
Phase 4: Record the decision
```

### Phase 0 — Classify + load context

| Type | Depth | Skip |
|---|---|---|
| **Implement** (a plan step / milestone) | research -> code -> test -> verify | — |
| **Fix** (bug, wrong output, crash) | locate -> diagnose -> minimal fix -> regression test | Phase 1 if location known |
| **Design** (open question / the plan itself) | research -> options table -> recommend -> write doc | Phase 3 tests |
| **Research** (verify a current lib/version/API) | parallel search -> synthesize -> report | Phase 2–3 |

Load context: read the relevant section of the v2 doc, the matching v1 as-built section for any
component you will touch, and the relevant `IMPLEMENTATION_PLAN.md` step. Pull current library
facts from the web at implement time — do not trust version-specific commands baked into the
docs (Ultralytics/pycoral/onnx2tf move fast); the docs give the approach, the web gives today's flags.

### Phase 1 — Parallel research (when needed)

Launch independent explorations in a **single message** (parallel subagents/Task calls), each
with the task context + specific questions. Use it for: current Ultralytics export flags, pycoral
API on this runtime, picamera2 patterns on Bullseye/Pi 4, controller/tracking references. Skip it
when the two design docs already settle the question — most do.

### Phase 2 — Execute

**Implement** — read the plan step, follow existing v2 patterns, write code + unit tests
together, keep changes inside the v2 tree. Honor the Hard Constraints below.

**Fix** — reproduce the failure, locate with Grep/Read, find the root cause, make the *minimal*
change, add a regression test. Don't refactor while fixing.

**Design / Plan** — synthesize findings, lay out 2–4 options in a table (Complexity S/M/L · Fits
v2 design yes/partial/no · Risk H/M/L · Where it runs Mac/Strix/Pi), recommend one with rationale,
write the deliverable to `.claude/claude-docs/plans/<topic>-{adr|plan|research}.md`. **Building the implementation
plan** is a Design task: turn the v2 doc's phased migration table into `.claude/claude-docs/IMPLEMENTATION_PLAN.md`
where every step has — goal, files to touch, which machine runs it, validation criteria, rollback.
Keep the phase order from the v2 doc: Phase 0 venv → Phase 1 fire-at-any-bird (NCNN single-class +
correct v8 decode + closed-loop aim + non-blocking fire) → Phase 2 correct Edge-TPU path → Phase 3
UX → Phase 4 species + human interlock. Build only what the current phase needs.

**Research** — parallel search, synthesize, report with confidence + gaps. No code.

### Phase 3 — Verify (the split matters)

**Mac-runnable (fast, pure logic — write these as `pytest`):** v8 decode (transpose `[1,5,8400]`,
no objectness multiply), NMS, pixel↔angle calibration transform, the P/PI controller step, the
SEARCHING→TRACKING→FIRING→COOLDOWN state machine, water-drop/parallax offset math. Unit-test the
decode against an Ultralytics `model.predict` reference on a saved frame — this is the guard
against re-introducing the v5/v8 mismatch.

**Strix Halo:** export produced a file ending `_edgetpu.tflite`; `edgetpu_compiler` log shows ops
on Edge TPU ≫ ops on CPU and one subgraph; Netron shows int8 in/out and output shape `[1,5,8400]`.

**Pi-only truth:** module imports with no hardware side effects; `model.predict` sane on a real
frame; measured FPS/latency recorded; servo dry-run within clamps (no water); decoy fire test hits
≥ the plan's target/10. Don't claim a perf or aiming result that wasn't measured on the Pi.

If a test fails, fix it before reporting success.

### Phase 4 — Record the decision

After meaningful work, append a short entry to `.claude/claude-docs/DECISIONS.md` (or the relevant ADR): what was
decided, files touched, gotchas found, and any number measured on-device. Keep `CLAUDE.md` current
if a constraint or workflow changed.

## Hard Constraints (violations are bugs)

- **Never modify v1 in place.** v2 lives in its own dir + venv; v1 stays runnable as rollback.
- **The detector is anchor-free YOLOv8/YOLO11**: output `[1, 4+nc, 8400]`, **no objectness
  channel**. Single class = `[1, 5, 8400]`. Decode = transpose → `boxes=out[:,:4]` (xywh ×input) →
  `scores=out[:,4:]` → threshold → NMS. **Never apply a YOLOv5 (anchor/objectness) decoder.** This
  was the v1 Coral accuracy bug.
- **`edgetpu_compiler` runs only on the Strix Halo x86-64 box.** Never attempt it on the Pi or Mac.
- **Compiled model file must end `_edgetpu.tflite`** or Ultralytics/runtime silently uses CPU and
  the Coral is bypassed.
- **INT8 export needs >300 representative calibration images** matching deployment imagery.
- **Fire is non-blocking.** No `time.sleep` in the control loop. Pump on → timer → pump off →
  COOLDOWN. Keep tracking throughout.
- **Pump is driven through a relay/MOSFET with a flyback diode, never a bare GPIO pin.** Servos get
  a separate 5–6 V supply with common ground; never the Pi 5 V rail.
- **PCA9685: init once; do not toggle MODE2 on/off per move.** `setPWM` already `int()`-coerces —
  the legacy float/`&` `TypeError` is already fixed; don't "re-fix" it.
- **Keep the clamps: pan 5–47°, tilt 5–25°.** MG996R pulse band ~1000–2000 µs.
- **Guard every module-level run block with `if __name__ == '__main__':`.** v1's `TurretHandler.py`
  ran a full detection loop at import — do not reproduce that.
- **Python 3.9 on the Pi.** No `match` statements, no `X | Y` union types, no 3.10+ syntax in code
  that runs on-device.
- **picamera2 lores on Pi 4 must be YUV420** (RGB lores is Pi 5 only). Size lores to the model input
  to avoid resize cost.
- **Stay on `libedgetpu1-std`** unless active cooling is added (`-max` overheats unattended).

## Tool priority

| Task | Primary | Fallback |
|---|---|---|
| Read / navigate code | `Read`, `Grep`, `Glob` | — |
| Edit code | `Edit` | `Write` (new files only) |
| Shell (git, pytest, build) | `Bash` | — |
| Push to Pi / run on-device | `Bash` → `ssh`/`rsync jayson@pi-jayson.local` | scp |
| Model build/export/compile | `Bash` on the Strix Halo box (document the command) | Docker |
| Current library/version facts | `WebSearch` | parallel research subagents |

## Anti-patterns

| Don't | Do |
|---|---|
| Implement without reading the plan step + v1 as-built section | Read both first |
| Apply a v5 decoder to a v8 model | Use the anchor-free decode; unit-test vs `model.predict` |
| Run `edgetpu_compiler` on Pi/Mac | Compile on the Strix Halo x86-64 box |
| Claim FPS/accuracy from the Mac | Measure on the Pi; record the number |
| `time.sleep` in the loop to fire | Non-blocking timer + state machine |
| Modify v1 to "save time" | Build in the v2 tree; keep v1 as rollback |
| Bake doc commands as gospel | Verify current flags on the web at implement time |
| Refactor while fixing a bug | Minimal fix + regression test |
| Report success with failing tests | Fix failures first |
