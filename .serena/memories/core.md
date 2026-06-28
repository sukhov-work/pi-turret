# mem:core — pi-turret graph root

## What this is
Raspberry Pi 4 + Coral USB autonomous pan/tilt **bird-deterrent turret** (Python, synchronous —
not async). Pi Camera → single-class bird detection → servo aim → fire (pump/laser). **v1 works and
is the rollback; v2 is built alongside it, and v1 is never edited in place.**

## Three machines (detail: mem:project/dev_environment, access: mem:project/machine_access)
- **Mac M3** — author + pure-logic `pytest` + git; **source of truth**. No hardware/TPU truth; can't run `edgetpu_compiler`.
- **Strix Halo (Ubuntu, x86-64)** — train/export/INT8/`edgetpu_compiler`; reach `ssh strix`. Only box that compiles Coral models.
- **Pi 4 (Bullseye)** — only source of camera/Coral/servo/FPS/aiming truth; reach `ssh pi`.
- Access over Tailscale (creds in `.claude/.env`, gitignored). **Deploy = `git push pi|strix main`** (push-to-checkout to `~/pi-turret`), not rsync.

## Source layout
- v1 (do NOT edit): all under `v1/` — `v1/main.py`, `v1/TurretHandler.py`, `v1/YOLOv8.py`, `v1/PCA9685.py`,
  `v1/Utils.py`, `v1/models/`, `v1/edgetpu-yolo/`, `v1/mjpg-streamer/`, `v1/index.html`. Run: `cd v1 && python3 main.py`.
- v2 (build here): own tree + venv, layered `detect/ track/ strategy/ aim/ actuate/ app/` (per IMPLEMENTATION_PLAN).

## Key invariants (violations = bugs)
- Never edit v1 in place. No hardware init or run loop at import — guard with `if __name__ == '__main__'`.
- Detector is anchor-free YOLOv8/11 `[1,4+nc,8400]`, NO objectness; never a YOLOv5 decoder (was the v1 Coral bug).
- Fire is non-blocking; pump/laser OFF on every exit path; conservative human interlock (never fire on a person).
- Clamp pan 5–47° / tilt 5–25° before every servo write; PCA9685 init once (no per-move MODE2 toggle).
- Servos on a separate 5–6 V supply (common ground); pump via relay/MOSFET + flyback diode, never bare GPIO.
- Python 3.9 on the Pi — no `match`, no `X | Y` unions on-device. Edge-TPU file must end `_edgetpu.tflite`.
- Any hardware/FPS/aiming claim is UNVERIFIED until measured on the Pi.

## Authority
`.claude/claude-docs/IMPLEMENTATION_PLAN.md` wins over `V2-design-plan.md` where they disagree.
Conventions: `.claude/conventions/`. Workflow: the `/turret` skill.

## Related memories
- `mem:tech_stack` — runtime, deps, tooling
- `mem:suggested_commands` — run / test / deploy / export commands
- `mem:task_completion` — quality gate before claiming done
- `mem:project/dev_environment` — three-machine parity, what can't be tested locally
- `mem:project/machine_access` — how the Mac reaches/operates/deploys to the boxes (anonymized; creds in `.env`)
- `mem:memory_maintenance` — how to maintain this graph
