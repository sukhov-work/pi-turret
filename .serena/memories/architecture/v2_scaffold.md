# mem:architecture/v2_scaffold — v2 as-built scaffold (Phase 0 + P1 spine)

Durable, non-obvious facts about the v2 code scaffolded 2026-06-27. Code is authority;
re-read files before trusting specifics. (Related: `mem:core`, `mem:tech_stack`.)

## Layout & tooling
- **v2 lives at the REPO ROOT**, not a subdir. Top-level packages: `detect/ track/ strategy/
  aim/ actuate/ app/`, plus `config.py contracts.py errors.py capture.py main.py`. Chosen
  because the conventions import as `from detect import …`/`from config import …`; it deploys via
  **`git push pi|strix main`** (push-to-checkout) to `~/pi-turret` on each box. v1 stays quarantined in `v1/` (untouched).
- Mac test venv: **`.venv-v2`** (Python 3.9.6 — Pi parity). Run: `.venv-v2/bin/python -m pytest -q`.
  `pytest.ini` sets `pythonpath = .`; deps numpy + PyYAML + pytest (`requirements-v2.txt`).
- Test count at scaffold time: **129 passed, 1 skipped** (the skipped one is the real-model
  golden test, gated on a fixture from Strix/Pi).

## Contracts & key modules
- `contracts.py` freezes `Detection(cls_id,score,xyxy,cx,cy)` and `Track(+id,vx,vy,age,hits,
  time_since_update)` in **full-frame pixels** — the stable seam; threads/backends depend only on this.
- `detect/decode.py::decode_v8` is the anchor-free decode (transpose→boxes→class scores→NMS,
  **no objectness multiply**). Has a `coords_normalized` flag (default False = input-pixel,
  Ultralytics default). **This flag is UNVERIFIED** until the golden fixture exists; the test
  `test_v8_decode_matches_ultralytics_reference` skips until `tests/fixtures/raw_output.npy` +
  `predict_ref.json` are captured on Strix/Pi.
- Tracker = **lightweight greedy-IoU + constant-velocity** (`track/tracker.py`), deliberately
  NOT ByteTrack/scipy (zero deps, Py3.9, Mac-testable). Confirmed tracks keep being surfaced
  while coasting within `max_age_frames` so the predictor extrapolates through occlusion.
- `app/control.py::ControlLoop.tick` is the **single servo mover** + fire gate. Default aim =
  calibration feed-forward (`aim/calibrate`) + `slew_toward` per-tick cap. `PIController`
  (pixel-centering) exists + is tested but is the *alternative* trim mode, not the default path.
- `app/statemachine.py::FireStateMachine`: SEARCHING→TRACKING→AIMING→FIRING→COOLDOWN→SEARCHING
  + SAFE. Non-blocking (injected clock); `off_fire` called on **every** exit from FIRING.
  `fire.enabled=False` ⇒ `last_would_fire` telemetry but never actuates (safe-demo default).

## Hardware ports (authored on Mac, lazy imports, Pi-verify)
- `actuate/pca9685.py` ports v1 driver: `smbus` imported lazily in `__init__`, **init-once**
  (`setup()` sets freq + MODE2 once; no per-move MODE2 toggle), keeps `int()` coercion.
- `actuate/servo.py::ServoController` clamps **angle AND pulse** every write; PAN=ch1, TILT=ch0;
  raises `ServoError` on driver failure; `disarm()` centers+relaxes.
- `actuate/pump.py::Pump`: non-blocking `fire()` via injected `timer_factory`; OFF on every
  error path; gpiozero lazy.
- `detect/coral.py::CoralDetector` wires `decode_v8` to the Edge-TPU interpreter (pycoral/tflite
  lazy); warns if model_path doesn't end `_edgetpu.tflite`.
- **Operator I/O (reuses v1 pins — see `mem:architecture/wiring`):** `actuate/lcd.py::StatusLcd`
  (fail-safe device) + `app/display.py` (`format_lcd_lines` pure + `LcdReporter` low-rate thread) =
  lifecycle LCD. `actuate/indicators.py::GpioOutput` = status LED (BCM23) + opt-in aux marker (BCM27),
  toggled in `ControlLoop`. `app/remote.py` = PROPOSED IR remote seam (evdev/rc-core, lazy) +
  `RemoteConfig`. Pipeline tracks fps + shots for the LCD. All fail-safe; OFF on disarm.

## Open flag — servo pulse band
Docs say MG996R ~1000–2000 µs, but v1's real operating pulses are **~556–1023 µs** (mapping
`deg*2000/180 + 501`). v2 keeps v1's mapping + a wide `[500,2500] µs` guard in `ServoConfig`.
A naive 1000–2000 clamp would BREAK v1's working aim. Re-measure on the Pi before narrowing.

## Not done (need other machines) — next steps
Strix: YOLOv8n@256 export + `edgetpu_compiler` gate + capture the golden fixture. Pi: camera
FPS, Coral latency, calibration fit, servo dry-run, decoy fire. Still to author: web UI (1.11),
USB streamer (1.12), motion-gating seam (1.10).
