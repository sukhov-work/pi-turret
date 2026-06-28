# pi-turret v2 — Implementation Plan (Draft 1)

*Status: first actionable draft, meant to be refined in place with Claude Code + the `/turret` skill. Source of truth for **what to build**; the v2 redesign doc and the two research reports are the **why**; the v1 as-built doc is **what exists**. Where this plan and a design doc disagree, this plan wins (it is newer and scope-narrowed). Every step lists: goal, files, machine, validation, rollback.*

## 0. Scope and decisions locked for Phase 1

**Phase 1 mission:** reliably and quickly put water on **any bird**, fast and accurately, with live tracking, prediction, multi-target handling, tunable strategy, headless operation, and the data/UX hooks needed for later phases.

In scope (P1): single-class "bird" detection correctly adapted to the Coral; tracking + constant-velocity prediction; multi-target scoring/selection/switching; tunable behavior via config; trip-wire kill-zone + predictive-lead firing (non-blocking); headless console mode with opt-in annotation (full video OR fire-decision frames only); detection snapshot capture for future training; USB webcam as the streaming source (Pi does not render Pi-Camera frames in live mode); web UI to switch camera/mode and tune strategy.

Explicitly deferred (later phases, but leave seams): species classification (pigeon/etc.), human-safety interlock, deterrent escalation, scheduling, dashboards.

**Locked decisions (override in place if you disagree):**
- **D1 — Detector is chosen by a compile gate, not assumed.** Primary: single-class YOLOv8n INT8 @256, correct anchor-free decode. Fallback: SSDLite-MobileDet @320 (clean Edge-TPU op-map, 9.1 ms, 32.9 mAP). Both expose the **same `Detection` output contract**, so nothing downstream depends on which won. (Research Part A: YOLO head TRANSPOSE/SOFTMAX may split to CPU and cost 1000+ ms; MobileDet maps cleanly and fits SRAM.) **2026 research update (see `plans/coral-detector-selection-research.md`):** the safer production default is to **invert** this — ship MobileDet@320 first (the only guaranteed clean single-subgraph compile) and treat single-class YOLOv8n@256 as the compile-gated upgrade; **SpaghettiNet-EdgeTPU** is the top accuracy-at-equal-latency candidate worth a bake-off.
- **D2 — Firing model is trip-wire kill-zone + predictive lead**, not reactive visual servoing. Water time-of-flight (~0.3–0.6 s) + servo travel dominate the budget; you get one led shot. Tracking/scoring run full-frame; the kill-zone is only the fire gate.
- **D3 — Detection runs on a motion-gated path is OPTIONAL in P1.** Build the detector path first (full-frame TPU each tick). Leave a clean seam for the motion-first/ROI-confirm optimization (research's top accuracy+latency win) as Step 1.10, behind a config flag, so it can be added without touching the tracker/controller.
- **D4 — Headless by default.** No frame rendering in live mode. Annotation/streaming are opt-in and run on a separate thread that can be killed without affecting the control loop.
- **D5 — Web stays Bottle.** Extend it; do not rewrite.
- **D6 — Pi runs Python 3.9.** No `match`, no `X | Y` unions in on-device code. v2 lives in its own tree + venv; v1 is untouched.

## 1. Architecture (target for Phase 1)

Pipelined, not serial. One process, threads sharing **lock-protected single-slot "latest value" buffers** (drop stale, never queue-backlog).

```
[Capture thread]      picamera2 lores YUV420 @model-input  --> latest_frame
[Inference thread]    detector(latest_frame) -> decode -> tracker.update()  --> latest_tracks
[Control thread]      strategy.select(latest_tracks) -> predict -> kill-zone gate
                      -> servo P-step (one-directional) -> fire state machine     (the only servo mover)
[Actuation]           PCA9685 servos (init once) + pump via relay/MOSFET (non-blocking)
[Annotation thread]   OPT-IN: draw boxes/tracks/state on a copy -> MJPEG / snapshots   (kill-safe)
[Web thread]          Bottle: mode/camera switch, strategy tuning, telemetry, debug view
[USB-cam streamer]    separate process/encoder; default live stream source (no Pi-Cam render)
```

**State machine (control thread):** `SEARCHING -> TRACKING -> AIMING -> FIRING -> COOLDOWN -> SEARCHING`. Fire is non-blocking: pump-on, start timer, keep tracking; pump-off on expiry; COOLDOWN debounces re-fire. No `time.sleep` in the loop.

**Module layout (suggested; refine in place):**
```
<repo-root>/            # v2 lives at the repo ROOT (not a subdir); v1 stays quarantined under v1/
  config.py            # dataclass config, loaded from config.yaml; all tunables live here
  capture.py           # Camera abstraction: PiCamCapture (detection), UsbCapture (stream/spotter)
  detect/
    base.py            # Detector ABC + Detection dataclass (the output contract)
    yolo_coral.py      # YOLOv8n INT8 Edge-TPU, correct anchor-free decode + NMS
    mobiledet_coral.py # SSDLite-MobileDet fallback, same contract
    decode.py          # pure-logic decode + NMS (unit-tested on Mac)
  track/
    tracker.py         # ByteTrack wrapper or lightweight IoU+Kalman; stable IDs
    predict.py         # constant-velocity (later CA) lead predictor (pure logic)
  strategy/
    scoring.py         # per-track threat/priority score (pure logic, tunable weights)
    selector.py        # pick + switch target; hysteresis to avoid thrash
  aim/
    calibrate.py       # pixel<->angle transform (fit + apply); parallax + drop offsets
    controller.py      # P/PI pixel-error step; one-directional backlash approach
    killzone.py        # fire-gate geometry (pure logic)
  actuate/
    pca9685.py         # init-once servo driver (port v1's, drop per-move MODE2 toggle)
    pump.py            # gpiozero relay/MOSFET, non-blocking timed fire
  app/
    statemachine.py    # SEARCHING..COOLDOWN
    pipeline.py        # thread wiring + latest-value buffers
    annotate.py        # opt-in drawing; full-video | fire-frames-only | off
    snapshots.py       # save detection crops+meta for future training
    web.py             # Bottle: control, tuning, telemetry, debug stream switch
  main.py              # entry; guarded by if __name__ == '__main__'
  tests/               # pytest (Mac-runnable pure logic)
  config.yaml
```

## 2. Phase 0 — Foundation (do first)

| # | Step | Goal | Files | Machine | Validation | Rollback |
|---|---|---|---|---|---|---|
| 0.1 | v2 tree + venv | Isolated v2; v1 untouched | v2 pkgs at **repo root**, `.venv-v2` | Mac + Pi | v1 still runs (`cd v1 && python3 main.py`); v2 venv imports picamera2, tflite-runtime/pycoral, numpy | delete tree/venv |
| 0.2 | Config skeleton | All tunables in one typed place | `config.py`, `config.yaml` | Mac | loads, validates, round-trips | n/a |
| 0.3 | Detection contract | Freeze `Detection`/`Track` dataclasses so threads can be built in parallel | `detect/base.py`, `track/tracker.py` stubs | Mac | importable; documented fields | n/a |

`Detection` contract (freeze early): `cls_id:int, score:float, xyxy:tuple[float,float,float,float], cx:float, cy:float` in **full-frame pixel coords** (detector maps back from model input). `Track` adds `id:int, vx:float, vy:float, age:int, hits:int, last_seen:int`.

## 3. Phase 1 — Steps

### 1.1 Correct Coral detector + decode gate  *(the blocker — do before anything downstream)*
- **Goal:** a single-class "bird" INT8 model running on the Edge TPU with a **correct anchor-free decode** (`[1,5,8400]` → transpose → `boxes=out[:,:4]` xywh×input → `scores=out[:,4:]` → threshold → NMS; **no objectness multiply, no YOLOv5 path**).
- **Files:** `detect/decode.py`, `detect/yolo_coral.py`, `tests/test_decode.py`.
- **Machine:** export/compile on **Strix Halo (x86-64 only)**; run on **Pi**; unit-test decode on **Mac**.
- **The gate:** read the `edgetpu_compiler` log. If the YOLOv8n head maps to **one subgraph, ops on TPU ≫ CPU** → keep YOLOv8n. If it **splits** (TRANSPOSE/SOFTMAX to CPU, "more than one subgraph") → switch to Step 1.1b. Concretely, accept a candidate only if `edgetpu_compiler -s` reports **`Number of Edge TPU subgraphs: 1`** and **`Off-chip memory used for streaming: 0.00B`**; export YOLO with **`nms=False dynamic=False`** to map the most ops. (Candidate matrix + the recommended MobileDet-default: `plans/coral-detector-selection-research.md`.)
- **Validation:** decode unit-tested against an Ultralytics `model.predict` reference on a saved frame (guards the v5/v8 mismatch forever); on-Pi `model` returns sane boxes on a real frame; measured inference latency recorded.
- **Rollback:** v1's ONNX-CPU detector still exists in the v1 tree.

### 1.1b SSDLite-MobileDet fallback *(only if 1.1 gate fails)*
- **Goal:** SSDLite-MobileDet @320 single-class, same `Detection` contract.
- **Files:** `detect/mobiledet_coral.py`.
- **Validation:** clean op-map in compiler log; sane boxes on Pi; latency recorded. Downstream code unchanged (same contract).

### 1.2 Camera capture (detection path)
- **Goal:** picamera2 **lores YUV420** sized to the model input (no resize/letterbox cost); fixed manual focus at engagement distance; capped exposure to limit motion blur; publish latest frame to a single-slot buffer.
- **Files:** `capture.py`.
- **Machine:** Pi.
- **Validation:** sustained capture FPS measured; Y-plane greyscale path verified; no AF hunting.
- **Rollback:** fall back to a higher-res RGB capture if lores config misbehaves.

### 1.3 Tracking + IDs
- **Goal:** stable multi-target IDs across frames. Start with Ultralytics ByteTrack if the chosen detector integrates cleanly; otherwise a lightweight IoU + Kalman tracker (works with any `Detection` source, fewer deps). Low `conf`/`track_high_thresh` to retain faint birds; tunable `track_buffer` for brief occlusion.
- **Files:** `track/tracker.py`.
- **Validation:** on a recorded clip, IDs stay stable through crossings/occlusions; unit-test the IoU/Kalman update on synthetic tracks.
- **Rollback:** single-target nearest-to-center if multi-track is unstable.

### 1.4 Prediction (lead)
- **Goal:** per-track constant-velocity predictor; estimate `(vx,vy)` px/frame; predict centroid at `t + (servo_travel + water_ToF)`. Leave a seam for constant-acceleration later.
- **Files:** `track/predict.py` (pure logic).
- **Validation:** unit tests on synthetic linear/curved tracks; prediction error bounded; degrades gracefully when a track is new (no velocity yet → no lead).
- **Rollback:** lead = 0 (aim at current centroid).

### 1.5 Scoring + multi-target selection/switching
- **Goal:** score each track by a **tunable weighted sum** (e.g. proximity to kill-zone center, size/closeness, dwell time, motion toward kill-zone, confidence); select the top; **switch only past a hysteresis margin** so the turret doesn't oscillate between birds.
- **Files:** `strategy/scoring.py`, `strategy/selector.py`, weights in `config.yaml`.
- **Validation:** unit tests with multi-track fixtures show expected pick + no thrash at the margin; weights changeable at runtime.
- **Rollback:** score = highest confidence only.

### 1.6 Calibration + aiming math
- **Goal:** replace v1's hand-tuned 25/15 coefficients with a **fitted pixel↔angle transform** (move to a grid of known angles, record pixel of a fixed target, fit affine/low-order poly). Separately encode **parallax** (camera vs nozzle offset) and **water-drop aim-above** vs estimated range.
- **Files:** `aim/calibrate.py` (fit + apply, pure-logic apply is unit-tested), `config.yaml` (stored transform).
- **Machine:** fit on **Pi** (needs the real rig); apply logic unit-tested on **Mac**.
- **Validation:** transform maps test pixels to angles within tolerance; documented procedure to re-run calibration.
- **Rollback:** keep v1 coefficients as a config preset.

### 1.7 Servo controller + actuation
- **Goal:** P (optionally PI) step from pixel error to angle delta; **one-directional final approach** to take up MG996R backlash consistently; small incremental steps per tick (less jitter/current spike); clamps **pan 5–47°, tilt 5–25°**; MG996R pulse band ~1000–2000 µs.
- **Files:** `aim/controller.py` (pure-logic step unit-tested), `actuate/pca9685.py` (port v1's, **init once, drop per-move MODE2 toggle**; keep its existing `int()` coercion — the float bug is already fixed).
- **Machine:** controller logic on Mac; servo dry-run on Pi (no water).
- **Validation:** controller step unit-tested for convergence/no-overshoot at chosen Kp; on-Pi dry-run stays within clamps, approaches from one direction; settling time measured.
- **Rollback:** v1 servo path in v1 tree.

### 1.8 Fire path + state machine
- **Goal:** **non-blocking** pump fire through a **relay/MOSFET** (`gpiozero`), flyback diode on the load; `SEARCHING→TRACKING→AIMING→FIRING→COOLDOWN`; fire only when (target selected) ∧ (predicted position in kill-zone) ∧ (aim error < deadband) ∧ (cooldown elapsed). Pump-on → timer → pump-off; keep tracking throughout.
- **Files:** `app/statemachine.py`, `aim/killzone.py` (pure logic), `actuate/pump.py`.
- **Machine:** state machine + kill-zone on Mac; pump dry-run on Pi (LED first, then pump).
- **Validation:** state-machine transition unit tests; kill-zone geometry unit-tested; on-Pi LED stand-in fires only under the full predicate; no `time.sleep` in the loop (assert in review).
- **Rollback:** disable fire (telemetry-only "would-fire" mode) via config — also useful for safe demos.

### 1.9 Headless mode + opt-in annotation + snapshots
- **Goal:** default **console-only** (structured log lines: state, selected track, score, predicted lead, fire events). Opt-in annotation thread with three modes: `off` | `fire_frames_only` (save/stream only frames where a fire decision was taken) | `full_video`. Annotation runs on a copy, is kill-safe, and never blocks control. **Snapshot capture**: on detection (configurable: every detection / fire only / sampled), save crop + full frame + metadata (timestamp, track id, score, box, predicted lead) to a dataset dir for future training.
- **Files:** `app/annotate.py`, `app/snapshots.py`, logging in `app/pipeline.py`.
- **Machine:** Pi (latency impact measured); drawing logic testable on Mac.
- **Validation:** live mode shows **zero** Pi-Camera rendering cost (measured FPS unchanged vs annotation `off`); `fire_frames_only` saves exactly the decision frames; snapshots land with correct metadata.
- **Rollback:** annotation `off` is the default and the safe state.

### 1.10 (Optional, behind a flag) Motion-first / ROI-confirm
- **Goal:** the research's top accuracy+latency win — Y-plane frame-diff/MOG2 proposes ROIs, TPU confirms only the crop. Behind `config.detection.mode = full_frame | motion_gated`. Same `Detection` contract out, so tracker/strategy unchanged.
- **Files:** `detect/motion.py`, wire in `app/pipeline.py`.
- **Validation:** measured end-to-end detect latency drops; false positives acceptable after TPU confirm; A/B against full-frame on a recorded clip.
- **Rollback:** flip flag back to `full_frame`.

### 1.11 Web UI extensions (Bottle)
- **Goal:** keep v1 routes; add: **mode switch** (live/headless ↔ debug), **camera/stream switch** (USB-webcam stream default; Pi-Cam annotated debug view on demand), **annotation mode** select, **strategy tuning** (scoring weights, thresholds, kill-zone, cooldown) with live apply, and **telemetry** (current state, tracks, selected target, last fire, FPS/latency). Fire-disable ("would-fire") toggle for safe demos.
- **Files:** `app/web.py`, minimal JS in the existing UI assets.
- **Machine:** Pi.
- **Validation:** switching camera/mode does not stall the control loop; tuning changes take effect live; USB stream is served without the Pi rendering Pi-Cam frames.
- **Rollback:** v1 web routes remain functional.

### 1.12 USB-webcam streaming (separate from compute)
- **Goal:** the human-viewable live stream comes from the **USB webcam**, encoded independently (its own process/encoder), so the Pi spends no detection compute on rendering. The annotated Pi-Cam view is debug-only and opt-in.
- **Files:** streamer launcher in `app/pipeline.py` or a small sidecar; config for source select.
- **Machine:** Pi.
- **Validation:** live stream runs with annotation `off` and detection FPS unaffected; web UI can switch the displayed source.
- **Rollback:** fall back to v1's mjpg-streamer path if the new streamer misbehaves (it already exists in v1).

## 4. Future-phase seams to leave in place now (build the hooks, not the features)
- **Multi-class / species + human:** `Detection.cls_id` and the detector contract already carry class; keep the decode multi-class-capable even while training single-class, so swapping in a 3–4 class model is a model+config change, not a refactor. Human-safety interlock = a cheap gate in the fire predicate (Phase 4).
- **Training-data flywheel:** Step 1.9 snapshots are the dataset source; keep metadata rich enough to auto-label later.
- **Strategy plug-ins:** scoring/selection behind config so new strategies (nearest, largest, longest-dwell, escalation) drop in.
- **Deterrent escalation / scheduling / dashboard:** state-machine guards + telemetry already present; add later.

## 5. Test strategy
- **Mac-runnable pytest (pure logic, the bulk of confidence):** decode + NMS (vs `model.predict` reference), tracker update, predictor, scoring/selection (incl. hysteresis), calibration apply, controller step, kill-zone geometry, state-machine transitions, water-drop/parallax math.
- **Strix Halo:** export ends `_edgetpu.tflite`; compiler log shows one subgraph, ops on TPU ≫ CPU; Netron shows int8 in/out and expected output shape.
- **Pi-only (the truth):** module imports with no hardware side effects (`if __name__ == '__main__'` guards); sane detections on real frames; measured FPS/latency per stage; servo dry-run within clamps; pump LED-stand-in fire under full predicate; **decoy hit test ≥ target/10** (set the target in `config.yaml`).

## 6. Milestone exit criteria (Phase 1 "done")
1. Single-class bird detector on Coral, correct decode, op-map verified, latency measured on-Pi.
2. Live multi-target tracking with stable IDs + prediction; scoring picks and switches with no thrash.
3. Trip-wire kill-zone + predictive-lead fire, non-blocking, within clamps, fire-disable demo mode works.
4. Headless console default; opt-in `fire_frames_only`/`full_video`; **measured zero** Pi-Cam render cost in live mode.
5. Snapshot capture producing training-ready data.
6. USB-webcam stream + web mode/camera/strategy switching, control loop never stalled.
7. Decoy hit-rate ≥ target on-device; v1 still runs as rollback.

## 7. Open questions to resolve in place (don't block the draft)
- Does single-class YOLOv8n @256 survive the compile gate, or is MobileDet the path? (Step 1.1.)
- ByteTrack vs lightweight IoU+Kalman — which integrates cleaner with the chosen detector on Py3.9/Pi? (Step 1.3.)
- Measured per-stage latency budget on this exact Pi 4 (drives whether 1.10 motion-gating is needed for P1 or deferred).
- Real water-stream velocity/range → the lead and aim-above constants (Step 1.6).
- Snapshot volume/retention policy (SD wear) — sample rate vs fire-only.

## 8. Build status, wiring, and added steps (updated 2026-06-27)

Authored + unit-tested on the **Mac** (`.venv-v2`, Python 3.9.6) — **176 passed / 1 skipped**.
v1 untouched. v2 lives at the **repo root** (top-level packages; imports are `from detect import …`,
deploy is **git push-to-deploy**: `git push pi|strix main` → `~/pi-turret` on each box, never rsync — reach via `ssh pi`/`ssh strix` over Tailscale, mosh+tmux for long sessions; access details in `mem:project/machine_access`, creds in `.claude/.env`). Run tests: `.venv-v2/bin/python -m pytest -q`.

### Per-step status
| Step | Status | What finishes it (machine) |
|---|---|---|
| 0.1–0.3 foundation (tree/venv, `config`, `contracts`) | **DONE (Mac)** | — |
| 1.1 `decode_v8` + NMS + golden guard | **DONE (Mac logic)** | export+compile+latency + **capture golden fixture** (Strix/Pi) |
| 1.1b MobileDet fallback | TODO | Strix/Pi, only if 1.1 gate fails |
| 1.2 `capture.py` PiCam lores YUV420 | AUTHORED | FPS/focus truth (Pi) |
| 1.3/1.4 `IouTracker` + lead predictor | **DONE (Mac)** | tune on a recorded Pi clip |
| 1.5 scoring + hysteresis selector | **DONE (Mac)** | tune weights on-device |
| 1.6 calibration apply+fit (v1 preset) | **DONE (Mac)** | run the **fit on the Pi rig** |
| 1.7 controller + `pca9685` port + `ServoController` | **DONE (Mac logic)** | servo dry-run within clamps (Pi) |
| 1.8 `FireStateMachine` + `killzone` + `Pump` | **DONE (Mac)** | LED-stand-in + decoy fire (Pi) |
| 1.9 headless + `annotate`/`snapshots` | AUTHORED (partial) | measure zero render cost (Pi) |
| 1.10 motion-gated seam | TODO | behind `app.detection_mode` flag |
| 1.11 web UI (Bottle) | **DONE (Mac logic)** | verify routes/live-tuning on Pi (`app/web.py`, `app/web_ui.html`) |
| 1.12 USB-webcam streamer | **DONE (Mac logic)** | verify mjpg-streamer flags/device on Pi (`app/streamer.py`) |
| 1.13 LCD lifecycle display (`actuate/lcd.py`, `app/display.py`) | **DONE (Mac logic)** | verify on real LCD (Pi) |
| 1.14 status LED + aux marker (`actuate/indicators.py`) | **DONE (Mac)** | verify BCM23/27 (Pi) |
| 1.15 IR remote (`app/remote.py` seam + `RemoteConfig`) | **SEAM ONLY** | owner: confirm pin; capture keys (Pi) |

**Open flags:** (a) `decode_v8.coords_normalized` default is UNVERIFIED until the real-model golden
fixture lands (`tests/fixtures/raw_output.npy` + `predict_ref.json`); (b) servo pulse band — docs
cite ~1000–2000 µs but v1 actually runs ~556–1023 µs, so v2 keeps v1's mapping + a `[500,2500] µs`
guard (re-measure on Pi).

### Wiring map (FIXED — verified from v1 source, do NOT rewire)
From `v1/TurretHandler.py:40-51` + `v1/PCA9685.py:32`. v2 reuses every pin; **escalate before any rewire.**
New hardware is **additive on free pins only.**

| Function | Bus / pin | v2 owner |
|---|---|---|
| PCA9685 servo driver | I2C **bus 1** @ `0x40` | `actuate/pca9685.py` |
| 1602A LCD | I2C **bus 1** (`rpi_lcd`, ~`0x27`) | `actuate/lcd.py` |
| Pan / Tilt servo | PCA9685 **ch 1 / ch 0** | `ServoConfig` |
| Water pump (was "main laser") | GPIO **BCM 26** (relay/MOSFET + flyback) | `actuate/pump.py` |
| Aux laser / aim marker | GPIO **BCM 27** (opt-in) | `actuate/indicators.py` |
| Status LED | GPIO **BCM 23** | `actuate/indicators.py` |
| IR receiver (PROPOSED) | GPIO **BCM 17** (free; confirm) | `app/remote.py` |

Free pins besides BCM17: 4/5/6/12/13/16/18/19/20/21/22/24/25 + SPI block. BCM 2/3 = I2C; 23/26/27 in use.

### Step 1.13 — LCD lifecycle display *(done on Mac; verify on Pi)*
- **Goal:** surface useful info throughout the run on the 1602A (16×2): boot + LAN IP, then per state —
  SEARCHING (`SCAN <spin> <fps> / trk:N ARM|SAFE`), AIMING (`AIM#id e<err> / KZ:Y WF ARM`), FIRING
  (`FIRE! #id / shots:N`), COOLDOWN, SAFE. v1 only showed on/off + angles.
- **Files:** `actuate/lcd.py` (`StatusLcd`, fail-safe device), `app/display.py` (`format_lcd_lines` pure +
  `LcdReporter` low-rate thread, never blocks control). Wired in `main.py` + `Pipeline` (fps + shots).
- **Validation:** `format_lcd_lines` unit-tested (truncation + per-state content); on-Pi the LCD updates
  at `app.lcd_refresh_hz` and never stalls the control loop; LCD I2C errors are swallowed.

### Step 1.14 — Status LED + aux marker *(done on Mac; verify on Pi)*
- **Goal:** drive v1's BCM23 status LED (on while not SAFE) and BCM27 aux laser as an **opt-in** aim
  marker (default off — laser safety; `app.aux_marker_enabled`). Fail-safe, off on disarm.
- **Files:** `actuate/indicators.py` (`GpioOutput`, `gpiozero.LED` like v1); toggled in `ControlLoop`.
- **Validation:** unit-tested status-LED-tracks-state + fail-safe; on-Pi confirm BCM23/27 behavior.

### Step 1.15 — IR remote control *(PROPOSED — seam only)*
- **Goal:** start/stop + basic control (arm/disarm, toggle fire-enable, center, jog pan/tilt) from a simple
  IR remote (owner's old Arduino kit). v1 has **no GPIO inputs**, so this is purely additive.
- **Approach (decide on Pi):** rc-core + evdev via `dtoverlay=gpio-ir,gpio_pin=17` (recommended on
  Bullseye) → remote shows up as `/dev/input/eventN`; capture key names with `ir-keytable -t`. Alternatives:
  LIRC, or pigpio software decode (no overlay).
- **Files:** `app/remote.py` (`RemoteActions` ABC, `build_key_map` pure, `RemoteListener` evdev thread,
  lazy import), `RemoteConfig` in `config.py`, `TurretRemoteActions` in `main.py`.
- **GATE (owner):** confirm the receiver pin (proposed **BCM 17**) and add the dtoverlay; then capture the
  remote's key codes on the Pi and fill `RemoteConfig` key fields.
- **Validation:** `build_key_map` unit-tested; on-Pi a key-down dispatches its action without affecting the
  control loop; remote errors are best-effort (never crash control).
