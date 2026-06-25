# mem:task_completion

Quality gate before claiming a task done. (Referred by `mem:core`.)

## Mac-runnable (always)
1. `python -m pytest -m "not hardware and not slow" -v` — green, **including the decode-vs-reference test**.
2. Modules import with **no hardware side effects** (no camera/servo/loop at import).
3. Conventions honored (`.claude/conventions/`): clamp before write, non-blocking fire, failsafe-on-error, units in names.
4. Changes stay in the v2 tree; v1 untouched.

## Pi-only (when the change touches hardware/timing) — cannot be satisfied by running on the Mac
5. On-device: real `model.predict` sane on a frame; measured FPS/latency recorded; servo dry-run within clamps
   (pump/laser disconnected first); human interlock blocks a person frame; then a decoy fire test.
6. Never report a perf/aiming result that wasn't measured on the Pi.

## After
7. One-line entry in `.claude/claude-docs/DECISIONS.md`.
8. `write_memory` for durable, non-obvious findings (include any number measured on-device).
9. Update CLAUDE.md / conventions if a rule or workflow changed.
