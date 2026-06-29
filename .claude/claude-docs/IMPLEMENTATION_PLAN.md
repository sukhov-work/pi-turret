# pi-turret v2 — Implementation Plan (Draft 1)

*Status: first actionable draft, meant to be refined in place with Claude Code + the `/turret` skill. Source of truth for **what to build**; the v2 redesign doc and the two research reports are the **why**; the v1 as-built doc is **what exists**. Where this plan and a design doc disagree, this plan wins (it is newer and scope-narrowed). Every step lists: goal, files, machine, validation, rollback.*

## 0. Scope and decisions locked for Phase 1

**Phase 1 mission:** reliably and quickly put water on **any bird**, fast and accurately, with live tracking, prediction, multi-target handling, tunable strategy, headless operation, and the data/UX hooks needed for later phases.

In scope (P1): single-class "bird" detection correctly adapted to the Coral; tracking + constant-velocity prediction; multi-target scoring/selection/switching; tunable behavior via config; trip-wire kill-zone + predictive-lead firing (non-blocking); headless console mode with opt-in annotation (full video OR fire-decision frames only); detection snapshot capture for future training; USB webcam as the streaming source (Pi does not render Pi-Camera frames in live mode); web UI to switch camera/mode and tune strategy.

Explicitly deferred (later phases, but leave seams): species classification (pigeon/etc.), human-safety interlock, deterrent escalation, scheduling, dashboards.

**Locked decisions (override in place if you disagree):**
- **D1 — Detector chosen by a compile gate. ✅ RESOLVED 2026-06-29: single-class YOLOv8n INT8 @256 is the P1 detector** (live on the Coral). The gate passed (1 subgraph, all ops on TPU — the feared TRANSPOSE/SOFTMAX → CPU split did **not** happen on `edgetpu_compiler` v16.0 + current Ultralytics) and on-Pi latency is **16.99 ms / 59 FPS** (§9). SSDLite-MobileDet@320 stays a documented **fallback only** (same `Detection` contract) — not needed unless a future heavier model's on-Pi latency disappoints. (The earlier research bias toward a MobileDet default was overridden by our measured op-map + latency; see `plans/coral-detector-selection-research.md` for the original candidate matrix.)
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

**Module layout (as-built; v2 at the repo ROOT, v1 quarantined under `v1/`):**
```
<repo-root>/
  config.py            # nested-dataclass config + config.yaml loader; all tunables here
  contracts.py         # Detection / Track dataclasses — the stable cross-layer contract
  errors.py            # TurretError hierarchy (Config/Camera/Detection/Servo/Pump)
  capture.py           # PiCamCapture (detection) + UsbCapture; lazy picamera2
  detect/
    base.py            # Detector ABC
    coral.py           # CoralDetector — Edge-TPU YOLOv8 (int8 in/out) + decode_v8  [primary]
    decode.py          # anchor-free decode_v8 + NMS + frame-clip (pure, golden-tested)
    # mobiledet_coral.py — SSD fallback, NOT yet built (not needed; see §9)
  track/   tracker.py (IouTracker: greedy-IoU + const-velocity)   predict.py (CV lead)
  strategy/ scoring.py (tunable weighted score)   selector.py (hysteresis switch)
  aim/     calibrate.py (affine pixel↔angle, fit+apply)   controller.py (P/PI step)   killzone.py
  actuate/ pca9685.py (init-once)   servo.py (ServoController: clamp angle+pulse)   pump.py (non-blocking)
           lcd.py (StatusLcd, fail-safe)   indicators.py (status LED + opt-in aux marker)
  app/
    statemachine.py    # FireStateMachine: SEARCHING→TRACKING→AIMING→FIRING→COOLDOWN→SAFE
    control.py         # ControlLoop.tick — THE single servo mover + fire gate
    pipeline.py        # thread wiring + LatestSlot single-slot buffers
    display.py         # format_lcd_lines (pure) + LcdReporter (low-rate thread)
    annotate.py  snapshots.py  remote.py(IR seam)  streamer.py(USB mjpg)  web.py + web_ui.html (Bottle)
  main.py              # entry, guarded; atexit/SIGTERM disarm
  models/              # committed *_edgetpu.tflite (+ .pt) — ship via git push
  scripts/             # pi_detector_bench.py (on-Pi benchmark)
  tests/               # flat pytest (pure logic) + fixtures/generate_golden_fixture.py
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

### 1.1 Correct Coral detector + decode gate  *(✅ DONE + Pi-verified — superseded by §9)*
- **Goal:** a single-class "bird" INT8 model running on the Edge TPU with a **correct anchor-free decode** (`[1,5,N]` (N=1344 @256) → transpose → `boxes=out[:,:4]` xywh → `scores=out[:,4:]` → threshold → NMS → clip; **no objectness multiply, no YOLOv5 path**; for our edgetpu export xywh are normalized → `coords_normalized=True`).
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

### 1.3 Tracking + IDs  *(✅ built as `IouTracker`)*
- **Goal:** stable multi-target IDs across frames. **Decided + built:** a lightweight **greedy-IoU + constant-velocity** `IouTracker` (NOT ByteTrack, NOT Kalman/scipy — zero deps, Py3.9-clean, Mac-testable); confirmed tracks coast through occlusion within `tracker.max_age_frames`. Low `conf` retains faint birds.
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

## 7. Open questions
- ✅ RESOLVED — single-class YOLOv8n@256 survives the gate (1 subgraph) **and** is fast on-Pi (59 FPS) → it's the P1 detector, not MobileDet (§9).
- ✅ RESOLVED — tracker is a lightweight greedy-IoU + constant-velocity `IouTracker` (no ByteTrack, no Kalman, no scipy; Py3.9-clean, Mac-testable). (Step 1.3.)
- ⏳ PARTIAL — detector latency measured (17 ms); the rest of the per-stage budget (capture, servo travel, end-to-end FPS) still needs the Pi (drives whether 1.10 motion-gating is needed for P1).
- ⏳ OPEN — real water-stream velocity/range → the lead and aim-above constants (Step 1.6, Pi rig).
- ⏳ OPEN — snapshot volume/retention policy (SD wear) — sample rate vs fire-only.

## 8. Build status, wiring, and added steps (updated 2026-06-29)

Authored + unit-tested on the **Mac** (`.venv-v2`, Python 3.9.6) — **178 passed / 0 skipped**.
v1 untouched. v2 lives at the **repo root** (top-level packages; imports are `from detect import …`).
Reach the boxes via `ssh pi`/`ssh strix` over Tailscale (mosh+tmux for long sessions; access in
`mem:project/machine_access`, creds in `.claude/.env`). Run tests: `.venv-v2/bin/python -m pytest -q`.
**Deploy = commit + push to `pi`/`strix`/`origin` — see "Deployment" below.**

### Critical path & honest status (read first)
**The detector is DONE and Pi-verified (§9):** single-class YOLOv8n@256 on the Coral — **16.99 ms /
59 FPS**, sane boxes, golden decode test green. All Phase-1 **Mac-authorable** work is also done
(tracker, predictor, scoring/selection, calibration apply, controller, kill-zone, state machine,
pump, LCD, indicators, web UI 1.11, USB streamer 1.12) — **178 tests, 0 skipped**.
➡ **The remaining critical path is on-Pi VERIFICATION of already-authored code, not new logic:**
camera capture/FPS (1.2), servo dry-run within clamps (1.7), LED→pump decoy fire (1.8), real LCD
(1.13)/indicators (1.14), web + USB stream on the rig (1.11/1.12), then **end-to-end pipeline FPS +
calibration fit + decoy hit-rate**. Plus **model iteration** on real field data — the repeatable loop
is `claude-docs/MODEL_ITERATION.md`. Only seam still to author: motion-gating (1.10). Detail: status table below.

### Deployment (push-to-deploy + GitHub hub)
Mac is the source of truth. Three remotes (`git remote -v`): **`pi`** and **`strix`** push-to-checkout
into **`~/pi-turret`** on each box; **`origin`** is GitHub `sukhov-work/pi-turret`.

| Push | Target | Notes |
|---|---|---|
| `git push origin main` | GitHub `sukhov-work/pi-turret` | hub mirror |
| `git push strix main` | `yevhen@…:/home/yevhen/pi-turret` | push-to-checkout |
| `git push pi main` | `jayson@…:/home/jayson/pi-turret` | push-to-checkout; Pi reachability confirmed 2026-06-29 |

**The boxes only get code you've COMMITTED + PUSHED.** Push all three every deploy (they should stay at the
same HEAD). `rsync`/`scp` for big artifacts only (datasets, fixtures) — **committed models in `models/` ship
via the push**, no rsync. Reach via `ssh pi` / `ssh strix` (Tailscale; creds in `.claude/.env`).
**Gotchas (verified 2026-06-29):** (a) if `ssh pi` times out on a stale control socket, add
`-o ControlMaster=no -o ControlPath=none`; (b) push-to-checkout fails ("Could not update working tree") if a
box has untracked files in the path being checked out (e.g. fixtures the generator wrote into the box's
`tests/fixtures/`) — `rm` the identical untracked file on the box, then re-push.

### Gates before the Detector Build Track (§9) — ✅ all CLEARED 2026-06-29
- **Strix toolchain:** DONE — `edgetpu_compiler` v16.0 + `~/turret-ml` (uv, Py 3.12, ultralytics). (§9.0)
- **Model:** DONE — first finetuned single-class bird model trained (HUB) + compiled clean (1 subgraph,
  252 ops): `models/bird_yolov8n_256_int8_edgetpu_run1.tflite` (+ vanilla reference). (§9.1/§9.2)
- **INT8 calibration imgs:** DONE — the Roboflow set (1152 imgs) staged on Strix doubles as calib.
- **Remaining (§9.3→§9.6):** ✅ ALL DONE 2026-06-29 — golden fixture landed (coords_normalized=True) → run1 deployed to Pi → measured **16.99 ms / 59 FPS** full infer (sane boxes ✓) → integrated. YOLOv8n@256 is the P1 detector.

### Per-step status
| Step | Status | What finishes it (machine) |
|---|---|---|
| 0.1–0.3 foundation (tree/venv, `config`, `contracts`) | **DONE (Mac)** | — |
| 1.1 `decode_v8` + NMS + golden guard | **DONE + Pi-VERIFIED 2026-06-29** | golden fixture landed (coords_normalized=True); run1 on Pi: full infer **16.99 ms / 59 FPS** (TPU 12.16 ms), sane boxes ✓ |
| 1.1b MobileDet/SSD backend | **NOT NEEDED (fallback)** | YOLOv8n passed the gate + is fast on-Pi; add `detect/mobiledet_coral.py` only if a future model's Pi latency disappoints |
| 1.2 `capture.py` PiCam lores YUV420 | AUTHORED | FPS/focus truth (Pi) |
| 1.3/1.4 `IouTracker` + lead predictor | **DONE (Mac)** | tune on a recorded Pi clip |
| 1.5 scoring + hysteresis selector | **DONE (Mac)** | tune weights on-device |
| 1.6 calibration apply+fit + **on-rig calibration UI** (Set Home / travel limits / click→fit, persists to `config.local.yaml`) | **DONE (Mac); UI deployed `eb1e409`** | run the **fit on the real rig** + verify hit accuracy |
| 1.7 controller + `pca9685` port + `ServoController` | **DONE (Mac logic)** | servo dry-run within clamps (Pi) |
| 1.8 `FireStateMachine` + `killzone` + `Pump` | **DONE (Mac)** | LED-stand-in + decoy fire (Pi) |
| 1.9 headless + `annotate`/`snapshots` | AUTHORED (partial) | measure zero render cost (Pi) |
| 1.10 motion-gated seam | TODO | behind `app.detection_mode` flag |
| 1.11 web UI (Bottle) — **all 14 sections live-tunable + persistable, per-param ⓘ docs, DET-CAM debug video, manual FIRE, disarm-freeze, boot-SAFE** | **DONE; Pi-VERIFIED `eb1e409`** (config validates, routes import; browser UX owner-verified). Param reference: `claude-docs/PARAMETERS.md` |
| 1.12 USB-webcam streamer | **DONE + Pi-VERIFIED 2026-06-29** | `/dev/video0`=UVC cam; streamer launches → HTTP 200 MJPEG → stops; binary = v1's committed ARM `_build/mjpg_streamer` |
| 1.13 LCD lifecycle display (`actuate/lcd.py`, `app/display.py`) | **DONE (Mac logic)** | verify on real LCD (Pi) |
| 1.14 status LED + aux marker (`actuate/indicators.py`) | **DONE (Mac)** | verify BCM23/27 (Pi) |
| 1.15 IR remote (`app/remote.py` seam + `RemoteConfig`) | **SEAM ONLY** | owner: confirm pin; capture keys (Pi) |

**Open flags:** (a) ✅ RESOLVED 2026-06-29 — `decode_v8.coords_normalized` is **pinned `True`** by the
landed golden fixture (`tests/fixtures/raw_output.npy` + `predict_ref.json`); Ultralytics v8 tflite
exports emit normalized xywh. (b) servo pulse band — docs
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

## 9. Detector Build Track — ✅ **COMPLETE (2026-06-29)**

**Outcome: single-class YOLOv8n@256 INT8 is the P1 detector, live on the Coral.** Gate passed
(1 subgraph, all ops on TPU), golden fixture landed (`coords_normalized=True`), on-Pi **16.99 ms /
59 FPS**, sane boxes. SSDLite-MobileDet@320 stays a documented **fallback only** (same `Detection`
contract) — not needed. The decided answers to the original open questions: deploy = git
push-to-checkout to `~/pi-turret` on each box (active, not retired); `edgetpu_compiler` = apt global
v16.0 on Strix; a trained bird model is already deployed (no stock-COCO first-light needed).

The steps below are the **as-built record + gate numbers**. The **repeatable retrain/deploy loop**
(field data → annotate → train → export → fixture → deploy → measure) now lives in
**`claude-docs/MODEL_ITERATION.md`** — use that for run2+, not this section.

### 9.0 GATE — Strix toolchain setup ✅ **DONE 2026-06-29**
- **`edgetpu_compiler` v16.0** installed globally (apt, Coral repo `coral-edgetpu-stable`, signed-by
  keyring) — on PATH.
- **`~/turret-ml`** = **uv-managed Python 3.12.13** venv (uv 0.11.25 via pipx; Strix's only system
  Python is 3.13, which breaks the TF/onnx2tf export chain → 3.12 venv is the dodge). The venv has **no
  system pip** (uv-managed) — install with `uv pip install --python ~/turret-ml/bin/python …`; run tools
  as `~/turret-ml/bin/{yolo,python}`. `ultralytics`+`roboflow`+`onnx`+`onnxslim`+`pip` installed; TF/
  onnx2tf are auto-installed by `yolo export format=edgetpu` (smoke-test log `/tmp/mlbuild.log`).
- **GPU:** ROCm 7.1 + gfx1151 (Ryzen AI MAX 395) present for optional torch-ROCm training accel later.
- Strix `~/pi-turret` checkout is current (`6884880`). ultralytics 8.4.81, onnxruntime 1.27.0, TF `<=2.19.0`.

### 9.1 Model sourcing ✅ **DONE 2026-06-29** — first model trained + converted
- **run1 shipped to `models/`:** HUB run `test-8n-run-1.pt` (single-class bird) → exported on Strix →
  `models/bird_yolov8n_256_int8_edgetpu_run1.tflite` (+ `.pt` source). Gate passed (§9.2); deployed + measured on Pi (§9.4).
- **Existing models are JUNK** (owner): everything in `v1/models/` + on the Pi is **obsolete — do not
  ship**. Going from scratch.
- **Dataset:** owner's **Roboflow** set, **single class `['bird']`** (`jayson-x-an0sg/pigeons-single-class`):
  **1152 train / 196 val / 63 test**, YOLO-format. **Staged on Strix** at
  `~/turret-ml/datasets/pigeons-single-class/` (data.yaml fixed to absolute paths). Source on Mac:
  `~/Downloads/Pigeons Single class.yolov8`.
- **First model:** **YOLOv8n @256, single class** → output `[1,5,1344]` (N=1344 @256; matches `decode_v8` + the golden
  test; `config.detector.num_classes=1`). **Train via Ultralytics HUB cloud** (owner's choice, GPU/fast)
  *or* locally on Strix; the **edgetpu export+compile is on Strix regardless** (compiler is x86-local).
- **Training config (verified):** base `yolov8n`, **`imgsz=256` (NOT 640)** — deploy is locked at 256 by
  the Coral gate, so train==deploy (a 640 model's val mAP overstates real 256 accuracy and its small-bird
  gain is lost at 256 inference). **`epochs≈300, patience≈50`** (1152 imgs converge early; 1000/100 just
  wastes time). Keep default aug (mosaic, fliplr 0.5, hsv, close_mosaic 10). `single_cls` irrelevant
  (dataset already nc=1).
- **INT8 calibration:** the **Roboflow training images double as the >300-img calib set** — pass
  `data=~/turret-ml/datasets/pigeons-single-class/data.yaml int8=True` to `yolo export`.
- **Bring-up shortcut (optional):** stock COCO `yolov8n.pt` already smoke-tested the toolchain end-to-end
  (§9.0) — can capture the golden fixture (§9.3) before the trained model lands.
- **Bake-off:** YOLOv8n is the confirmed primary (§9.2 gate passed); MobileDet only if on-Pi latency
  disappoints. **Machine:** HUB/Strix (train) + Strix (export). **Rollback:** v1 ONNX-CPU detector in `v1/`.

### 9.2 Compile gate (Stage 0, Strix) — ✅ **YOLOv8n PASSED 2026-06-29**
- **Measured (stock yolov8n@256 INT8 smoke test, edgetpu_compiler 16.0):** **1 Edge-TPU subgraph,
  all 256 ops on the TPU (0 on CPU)**, on-chip 3.57 MiB / 3.23 MiB free, off-chip streaming 7.88 KiB
  (≈0). The v8 head ops the research feared (SOFTMAX/TRANSPOSE/STRIDED_SLICE/RESHAPE) **map cleanly
  on this toolchain** → **YOLOv8n is the confirmed primary; MobileDet is now only the fallback**.
- **YOLO export flags:** `nms=False dynamic=False imgsz=256 int8=True data=<roboflow>/data.yaml`.
- **✅ Finetuned single-class model PASSED too (run1, `test-8n-run-1.pt`, 2026-06-29):** **1 subgraph,
  252 ops all on TPU**, on-chip 3.11 MiB / 3.68 MiB free, off-chip 7.88 KiB → `models/bird_yolov8n_256_int8_edgetpu_run1.tflite`.
  Smaller head than stock 80-class (252 vs 256 ops) = more on-chip headroom, as predicted.
- **Accept if** `edgetpu_compiler -s` reports **`Number of Edge TPU subgraphs: 1`** and off-chip
  streaming ≈0 (the strict ideal is `0.00B`; a few KiB like our 7.88 KiB is fine — the catastrophic
  case is a *split tail* sending most ops to CPU).
- **Netron check:** int8 in/out; YOLO output `[1,4+nc,N]` (N=1344 @256; no objectness). Compiled file name
  must contain **`_edgetpu`** (we load via pycoral `make_interpreter`, which uses the TPU regardless of the
  exact name; run-versioned `..._edgetpu_run<N>.tflite` is fine).
- **Go/No-Go:** reject any candidate needing 2+ subgraphs or a split tail (Pi-4 CPU fallback ≈
  1800–2100 ms vs ~22 ms mapped). **Machine:** Strix.

### 9.3 Decode golden fixture *(un-skips the Mac guard test)* — ✅ **DONE 2026-06-29**
- **Done via `tests/fixtures/generate_golden_fixture.py`** (committed): on **Strix** it reads the literal
  dequantized tflite output (== what `coral.py` reads on the Pi) from run1's pre-compile INT8 twin
  `test-8n-run-1_full_integer_quant.tflite` + Ultralytics' own `predict` on the same val frame, then
  **self-pins** the flag by replaying the test for both values. Artifacts `raw_output.npy` (shape
  `[1,5,1344]`) + `predict_ref.json` pulled to the Mac and committed.
- **Pinned `coords_normalized=True`** (the `False` path mis-decodes by ~1104 px). Also added
  **frame-bound clipping to `decode_v8`** (matches Ultralytics `clip_boxes`; the only delta was an
  off-frame `y1`). `test_v8_decode_matches_ultralytics_reference` now green → **178 passed / 0 skipped**.

### 9.4 On-Pi latency + sanity *(Pi truth)* — ✅ **DONE 2026-06-29 → GO**
- **Measured** via `scripts/pi_detector_bench.py` (committed), run1 edgetpu, **USB3 SuperSpeed (5000M)**,
  `libedgetpu1-std`, system Py 3.9.2 + pycoral (no venv), 200 iters @256: **TPU invoke 12.16 ms (82 FPS);
  full `infer` 16.99 ms (59 FPS); decode+dequant ~4.8 ms**; warm `load()` ~2.7 s.
- **Sane boxes ✓** — Pi edgetpu on the val frame `(186.0,0.0,965.3,1098.6) score 0.622` ≈ the Strix-CPU
  fixture `(186.0,0.0,965.3,1109.2)` within ~10 px (TPU-vs-CPU int8) = full-stack decode validation.
- **Fixed on Pi truth:** `coral.py._preprocess` now quantizes for **int8-input** edgetpu models (was
  feeding uint8 → `ValueError`). **Go/No-Go: GO** — 59 FPS ≫ the 15–24 FPS control budget; **YOLOv8n@256
  is the P1 detector, MobileDet fallback not needed.** **Machine:** Pi.

### 9.5 Integrate into v2 *(same `Detection` contract — no downstream churn)*
- **YOLO path:** `detect/coral.py` (exists) + `detect/decode.py::decode_v8` (exists). Set
  `config.detector.model_path` (the `_edgetpu` model), `input_size_px`, `conf/iou`, `num_classes`.
- **SSD/MobileDet path:** add **`detect/mobiledet_coral.py`** — SSD post-process reads the
  `TFLite_Detection_PostProcess` outputs (boxes / classes / scores **directly**; **no YOLO transpose,
  no `decode_v8`**), filters to bird, emits identical `Detection`. Select via `config.detector.backend`
  (`coral_yolo | coral_mobiledet`). Add a small Mac unit test for the SSD post-process shape mapping.
- **Validation:** full Mac suite green; on Pi the pipeline runs with the chosen backend and switching
  `backend` needs no tracker/strategy/aim/UI change. **Machine:** Mac (author/test) + Pi (run).

### 9.6 Record + choose the P1 default — ✅ DONE
- **P1 default = YOLOv8n@256** (passed Stage 0 + 59 FPS on-Pi). DECISIONS + `mem:decisions/detector_build_plan`
  hold the measured row. Accuracy upgrades (only if needed): more/finetuned field data first
  (`MODEL_ITERATION.md`), then a larger input (320, re-gate) or SpaghettiNet-EdgeTPU; MobileDet is the latency fallback.

### Detector track exit criteria — ✅ all met 2026-06-29
1. One candidate compiles to **1 subgraph / ≈0 B off-chip**, file name contains `_edgetpu`.
2. Golden fixture committed; `test_decode` real-model test green on the Mac.
3. Measured Pi inference ms/FPS recorded; `CoralDetector` (or MobileDet) returns sane boxes on a real frame.
4. Backend selectable by config with zero downstream change; full Mac suite green; v1 still the rollback.
