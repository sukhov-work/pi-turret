# pi-turret v2 Redesign — Design Document

*Prepared for Yevhen (sukhov-work). Current date: June 2026. Hardware fixed; Debian 11 Bullseye + Python 3.9 retained. Build v2 alongside the working v1.*

> **Status & supersession (read first).** This is the *rationale / "why"* document. The authoritative
> *"what to build"* is **`IMPLEMENTATION_PLAN.md`**; **where this doc and the plan disagree, the plan wins**
> (it is newer and scope-narrowed). The plan has since locked these decisions, overriding the matching
> recommendations below:
> - **Phase-1 detector:** the plan makes the **Coral Edge-TPU YOLOv8n INT8 @256 the primary**, with an
>   **SSDLite-MobileDet @320 fallback**, chosen by a compile gate (plan D1). The "NCNN single-class on the
>   Pi 4 CPU as the Phase-1 backend" recommendation here is demoted to a fallback/option.
> - **Firing model:** the plan uses a **trip-wire kill-zone + predictive lead** (plan D2), *not* the reactive
>   closed-loop visual servoing proposed here. The P/PI controller survives only as the aim gate, not the firing paradigm.
> - **Streaming:** the plan keeps the **USB webcam as the default (headless) stream source**, with the annotated
>   Pi-Camera view debug-only and mjpg-streamer retained as a rollback (plan D4 / §1.12) — not the "remove
>   mjpg-streamer, stream annotated Pi-Camera frames" proposal in §D/§G.
> - **Edge-TPU model family:** **YOLOv8n** (not YOLO11n) for the Coral path, per the plan.
>
> The Edge-TPU pipeline mechanics, calibration/parallax math, concurrency model, and power/servo guidance below remain valid.

## TL;DR
- **The detection-accuracy collapse on the Coral path is a software decode bug, not a TPU or quantization failure.** `pigeon-y8s_edgetpu384.tflite` is a YOLOv8 model (anchor-free, output `[1, 4+nc, 8400]`, **no objectness channel**), but the bogdannedelcu/edgetpu-yolo `detect.py` decodes it as YOLOv5 (anchor-based, `[1, 25200, 85]`, objectness at index 4, channels-last). A v5 decoder applied to v8 output reads the wrong axes plus a channel that does not exist → garbage boxes even when the TPU compile and INT8 quantization are perfect. **Fix the decoder first; it is the single highest-leverage change.**
- **Recommended Phase-1 backend: NCNN single-class "bird" YOLO11n/YOLOv8n on the Pi 4 CPU** — Ultralytics-documented as the fastest Pi format, ~2× faster than ONNX, and it lets you delete the fragile custom OpenCV build. **Performance path:** a correctly re-exported INT8 YOLOv8n at 192–256 px on the Coral with proper v8 decode (lowest inference latency, but real op-mapping/accuracy caveats).
- **Aiming must move from open-loop single-shot to closed-loop visual servoing.** Add ByteTrack identity, a proportional (P/PI) pixel-error→servo controller that drives the target centroid to the calibrated nozzle impact point, a real pixel↔angle calibration, and a **non-blocking fire state machine** so the system keeps tracking during and after firing.

## Key Findings

1. **Root cause of "terrible" Coral accuracy (confirmed via primary sources).** YOLOv5 detection output is `[1, 25200, 85]` = `[cx,cy,w,h, objectness, 80 classes]`, channels-last, anchor-based, requiring sigmoid + anchor-grid decoding. YOLOv8 output is `[1, 84, 8400]` = `[4 box + 80 class]`, channels-**first** (transposed), anchor-**free**, with **no objectness channel** (Ultralytics issue #751: YOLOv8 "deletes 1 object score (85-1=84)"). A single-class v8 model is `[1, 5, 8400]`. The v5 decoder multiplies class scores by an "objectness" at index 4 and slices classes `5:85` on a channels-last tensor — on a v8 tensor index 4 is the *first class score*, the axes are transposed, and there is no objectness, so the output is meaningless. Correct v8 decode: dequantize → transpose `[1,5,8400]→[8400,5]` → `boxes=out[:,:4]` (xywh, normalized 0–1, ×input size), `scores=out[:,4:]` → confidence threshold → NMS (boxes already sigmoid/xywh; **no** objectness multiply).

2. **Quantization was likely also imperfect, but secondary.** Ultralytics `format=edgetpu` runs ONNX→`onnx2tf`→full-INT8 TFLite→`edgetpu_compiler`. Documented pitfalls: INT8 calibration needs a representative dataset — Ultralytics emits "⚠️ >300 images recommended for INT8 calibration, found N images" (GitHub issue #15873) when too few are supplied, producing poor quant scales; `nms=False`/`dynamic=False` give the cleanest Edge-TPU op-mapping; **the compiler is x86-64 only** — per the Ultralytics Coral guide, "the Edge TPU compiler is not available on ARM," so the user's on-Pi compile attempts could never have worked; and the file **must end `_edgetpu.tflite`** or Ultralytics silently loads it as a plain CPU TFLite model and the accelerator is never used.

3. **Input size is the binding Edge-TPU constraint.** Community evidence (Ultralytics discussion #8398, issue #4754): YOLOv8 exported >192×192 frequently segfaults or falls back to CPU on Coral; some ≥256 px configs historically "fail to compile due to large tensors"; models exceeding the Edge TPU's ~8 MB on-chip memory stream parameters from off-chip and lose the speedup. Ultralytics' own Coral benchmarks exist only at 320 and 512.

4. **NCNN is the biggest, lowest-risk CPU win.** The Ultralytics Raspberry Pi guide states verbatim: "Out of all the model export formats supported by Ultralytics, NCNN delivers the best inference performance when working with Raspberry Pi devices because NCNN is highly optimized for mobile/embedded platforms (such as ARM architecture)." Ultralytics' Pi 5 benchmark shows YOLOv8n NCNN at **94.28 ms vs ONNX 198.69 ms** (≈2× faster); LearnOpenCV reports NCNN conversion can "slash inference times by up to 62%."

5. **Tracking + closed-loop control is mature and cheap.** Ultralytics ships ByteTrack/BoT-SORT via `model.track(..., persist=True)`. A published pan-tilt PID face-tracker (Marquez et al., MDPI Eng. Proc. 113(1):75, 2024) reports "settling time for the pan axis was approximately 0.95 s, while the tilt axis stabilized within about 1.10 s … Overshoot remained below 5% … steady-state error consistently stayed under 4 pixels" — directly applicable to this turret.

## Details

### A. Model + Inference Backend (highest priority)

**Current path diagnosis.** Detection runs an INT-quantized ONNX YOLOv8 through **ONNX Runtime** on the CPU — *not* OpenCV `cv2.dnn` (`cv2.dnn` is used only for the SSD person check; verified in the v1 as-built doc §5/§13). The Coral is not in the hot path. ONNX-Runtime INT8 on the ARM CPU is slow, and the custom OpenCV 4.4.0 source build (Ubuntu Xenial libjasper hack) — still needed for the SSD check and box drawing — is the most fragile component in the stack.

**Option comparison (Pi 4 + Coral USB / Pi 4 CPU):**

| Backend | Model | Input | Inference latency | Fragility | Source basis / caveat |
|---|---|---|---|---|---|
| Coral Edge TPU (correct v8 decode) | YOLOv8n | 320 | ~32 ms std / ~27 ms max-clock | Med (compile/decode) | Ultralytics Coral bench, Pi 4B |
| Coral Edge TPU | YOLOv8n | 512 | ~73 ms std / ~61 ms max | Med | Ultralytics Coral bench, Pi 4B |
| Coral Edge TPU | YOLOv8n | 192 | < 320-px figure; accuracy drops | Med-High | "sweet spot" for op-mapping; some report no speedup if ops fall to CPU |
| **NCNN CPU** (recommended P1) | YOLO11n/YOLOv8n | 256–320 | multi-FPS; ~2× ONNX | **Low** | Ultralytics "best on Pi"; 94 ms vs 199 ms ONNX (Pi 5 ref) |
| ONNX/OpenCV-DNN CPU (current) | YOLOv8 INT | 384 | slow (baseline) | **High** | fragile OpenCV source build |
| OpenVINO / MNN CPU | YOLO11n | 640 | OpenVINO ~81 ms, MNN ~116 ms | Low-Med | LearnOpenCV (Pi-5-class figures; Pi 4 slower) |

All latency figures above are from Pi 4B (Coral) or Pi-5-class (NCNN/OpenVINO/MNN) references and **must be re-measured on this exact Pi 4**.

**Generic COCO "bird" vs finetuned single class.** Stock COCO has a "bird" class (index 14), but it is trained mostly on large, centered birds and will under-detect small/distant/perched garden birds. **Recommendation: a finetuned single-class "bird" model** — reuse the Roboflow pigeon/crow/magpie dataset, collapse all labels to one "bird" class. It will materially outperform COCO-bird at low input resolution, and single-class shrinks the head to `[1,5,8400]`, simplifying decode.

**Model family in 2026.** YOLOv8n and YOLO11n both export and run well via NCNN; YOLO11n is marginally leaner/faster at similar accuracy. For Edge TPU specifically, **YOLOv8n compiles more cleanly than YOLO11n** — multiple reports (Ultralytics issue #19336) show YOLO11's head ops ("More than one subgraph is not supported"; TRANSPOSE/RESHAPE/SOFTMAX) falling to CPU, while YOLOv8n maps the bulk of CONV_2D/LOGISTIC to the TPU. YOLOv5 is anchor-based with lower mAP and is only a fallback if v8 head ops won't map. **Net: YOLO11n (NCNN/CPU path) for reliability; YOLOv8n for the Edge-TPU path.** (Newer Ultralytics families exist but offer no Edge-TPU advantage here and add risk.)

> **2026 update (`plans/coral-detector-selection-research.md`).** A dedicated Coral-detector study recommends making **SSDLite-MobileDet@320 the production default** — the only detector with a guaranteed clean single-subgraph compile — with single-class YOLOv8n@256 as a compile-gated upgrade and **SpaghettiNet-EdgeTPU** as the top accuracy-at-equal-latency candidate. It also confirms **resolution, not model family, is the dominant small-bird accuracy lever** (INT8 AP_small roughly triples 320→640).

**Small-target reality.** YOLO mAP degrades sharply on objects <32×32 px and at low input resolution (multiple aerial/small-object studies). At 192–256 px Coral inputs, distant birds may be only a few pixels — expect misses. This argues for (a) a finetuned bird model, (b) the largest input size that still maps to the TPU, and (c) accepting the NCNN CPU path at 320–416 px when small-bird recall matters more than latency.

### B. Correct Edge TPU Pipeline (end-to-end, 2026)

Run all export/compile on the x86-64 Strix Halo Ubuntu 25 workstation (native or Docker). **Never on the Pi** — the compiler is x86-64-only. (The Mac M3 can run the Ultralytics Docker container for export but is also ARM, so use the Strix Halo box.)

1. **Train/finetune** single-class bird YOLOv8n: `yolo train model=yolov8n.pt data=bird.yaml imgsz=256 epochs=100`.
2. **Export to full-INT8 Edge-TPU TFLite:**
   `yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=bird.yaml nms=False`
   - Provide `data=` pointing at **>300 representative calibration images** matching deployment imagery (else Ultralytics warns and quant scales suffer).
   - Produces `best_full_integer_quant_edgetpu.tflite` in `best_saved_model/` (pipeline: ONNX→`onnx2tf`→`*_full_integer_quant.tflite`→`edgetpu_compiler`).
3. **Verify op mapping:** read the compiler log — want "operations that will run on Edge TPU" ≫ "…on CPU" and one subgraph. If most ops land on CPU, the model wasn't fully INT8 (input/output must be int8, not float) or the input size is too large.
4. **Verify the graph in Netron:** input dtype int8, output dtype int8, output shape `[1,5,8400]` (single class), head not left in FP32.
5. **Run on Pi** via tflite-runtime/pycoral with the **correct v8 decode** (Finding 1). Use **jveitchmichaelis/edgetpu-yolo**, which supports both v5 and v8 — switch off the bogdannedelcu v5-only path. Confirm the `_edgetpu.tflite` suffix so the accelerator is actually used.

**libedgetpu1-std vs -max.** The user runs `libedgetpu1-std` (throttled). `-max` is ~28% faster (Ultralytics: "The high-frequency mode is 28.4% faster than the standard mode") but the USB Accelerator "can become very hot to the touch," and Ultralytics warns high-frequency mode "can cause thermal throttling — use some form of cooling." For an unattended outdoor turret, **stay on -std unless you add active cooling**; sustained-load throttling erases the gain and risks hangs.

### C. Closed-Loop Aiming & Tracking

- **Tracker:** `model.track(source, persist=True, tracker="bytetrack.yaml")` for stable IDs. ByteTrack is faster than BoT-SORT and sufficient for a single target (no ReID needed). Set `conf` low (~0.1) / lower `track_high_thresh` to retain faint birds; tune `track_buffer` for brief occlusions.
- **Controller:** replace the open-loop pixel→angle jump with a **proportional (or PI) visual servo**: `error = (target_centroid − nozzle_impact_point)` in pixels; `Δpan/Δtilt = Kp·error`; iterate each frame until `|error| < deadband`, then fire. Start Kp low for stability given MG996R slop/backlash and ~0.14–0.19 s/60° speed; add a small integral term only if a steady offset persists; avoid the derivative term (noisy at low frame rates). Reference PID pan-tilt trackers reach ~1 s settling, <5% overshoot, <4 px steady-state error.
- **Predictive lead:** estimate centroid velocity (px/frame) from the tracker and lead by `velocity × (servo travel time + water time-of-flight)`. Given the narrow envelope (~42° pan / ~20° tilt) and short range, lead is **low priority** — a fast closed loop usually suffices; add lead only if fast fly-throughs are routinely missed.
- **Calibration (replace the crude 25 H / 15 V coefficients):** command the turret to a grid of known pan/tilt angles, record a fixed target's pixel location at each, and fit an affine/low-order-polynomial pixel↔angle transform. Separately calibrate **parallax**: the camera optical axis and nozzle are offset, so the pixel the camera centers is *not* where water lands — test-fire at a target board, record the wet-spot pixel, and servo the centroid to *that* point, not image center.
- **Water-stream ballistics:** the jet drops under gravity with limited range, so **aim above the target** by an amount that grows with distance. At short range the drop is small; encode a simple tilt bias vs estimated range, or a fixed upward offset tuned empirically at the engagement distance.

### D. Camera / Image Pipeline (picamera2 on Bullseye)

- **Dual-stream config:** a `main` RGB stream for the human MJPEG view plus a `lores` stream sized to the model's native input to eliminate resize cost. On **Pi 4 the lores stream must be YUV420** (RGB lores is Pi 5 only) — take the Y plane for fast greyscale, or use the supplied `YUV420_to_RGB`. The current 1152×1152 RGB888 continuous-AF capture is wasteful for detection.
- **Avoid letterbox/resize cost:** size lores to exactly the model input (256×256 or 320×320) so no CPU resize/letterbox runs before inference.
- **Focus:** continuous AF "hunts" on fast close birds and adds latency. Use **manual fixed focus** at the engagement distance: `picam2.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": L})`, where LensPosition is in dioptres (≈ 1/distance_in_metres; 0.0 = infinity, so ~1 m → ≈1.0). Calibrate empirically (the IMX708 mapping is per-unit). For a truly locked lens, you can even strip the `rpi.af` section from the imx708 tuning file.
- **Frame rate:** set `FrameDurationLimits` to a steady target (e.g. 30 fps) so the control loop has predictable timing.

### E. Concurrency / Software Architecture

Current `v1/main.py` (Bottle server + blocking while-True detection daemon + 50 ms `threading.Timer` control loop, detection loop blocking for seconds during fire) means nothing tracks during/after firing, and the PCA9685 is toggled on/exit per move. v2:

- **Use threads, not asyncio/multiprocessing.** Python 3.9's GIL is not a blocker here because the Coral/USB native invoke releases the GIL during the call (pycoral treats Edge TPU ops as I/O-bound), and NCNN inference is in native code. Layout:
  - **Capture thread** (picamera2 callback) → publishes the latest frame to a single-slot buffer.
  - **Inference+track thread** → reads latest frame, runs detector + ByteTrack, publishes latest detections.
  - **Control thread** → consumes detections, runs the servo P/PI loop + state machine.
  - **Actuation** → PCA9685 servos + pump GPIO, non-blocking, no per-move chip on/off.
  - **Web/stream thread** → serves UI + annotated MJPEG.
- **Share state via lock-protected single-slot "latest value" objects**, not queues — drop stale frames to avoid backlog and races.
- **State machine:** `SEARCHING → TRACKING → FIRING → COOLDOWN`. Fire is **non-blocking**: trigger pump-on, start a timer, keep tracking; pump-off on timer expiry; COOLDOWN prevents re-fire chatter. Remove all `time.sleep()` from the loop.

### F. Servo / Actuation Layer

- **Float/int bitwise bug — already fixed; do not re-fix.** `PCA9685.setPWM` already coerces with `int()`; the `TypeError: unsupported operand & between float and int` came from the stock Waveshare driver and is gone in this repo (confirmed in v1 as-built §13 #7 and plan Step 1.7).
- **Stop toggling the PCA9685 on/off per move:** initialize once and leave it running; the per-move start/exit pattern adds latency and jitter.
- **Use a maintained library:** `adafruit-circuitpython-pca9685` + `adafruit-circuitpython-servokit`. Set `pca.frequency = 50` (20 ms period, correct for MG996R). Map angles with `kit.servo[ch].set_pulse_width_range(min, max)`; keep MG996R in the **~1000–2000 µs** band (full ~500–2500 µs risks gear/winding damage). Retain the existing pan 5–47° / tilt 5–25° clamps.
- **Smooth motion:** step toward the target angle in small increments per control tick rather than large jumps — less jitter and lower current spikes.
- **Pump drive:** replace the old laser-LED on/off pattern. Drive the 5–12 V pump through a **relay or logic-level MOSFET** from a GPIO via `gpiozero` (e.g. `DigitalOutputDevice`), never directly off a Pi pin; add a flyback diode across the inductive load.
- **Power:** MG996R draws ~500–900 mA running and up to **~2.5 A stall at 6 V**. Use a **separate 5–6 V supply for the servos** (not the Pi's 5 V rail), common ground with the Pi, plus a bulk capacitor; give the pump its own supply. Otherwise brownouts will reset the Pi.

### G. Web / Streaming / Remote Control

mjpg-streamer with the custom `input_opencv.so` plugin (built because `input_raspicam.so` needs absent `/opt/vc` headers on 64-bit) is dated and fragile. v2:

- **Stream annotated detection frames directly from the Python app** (boxes + track IDs + state) using picamera2's `MJPEGEncoder` + a `StreamingOutput` (threading.Condition) over HTTP — the standard picamera2 `mjpeg_server.py` pattern. This removes mjpg-streamer and the OpenCV plugin entirely.
- **Keep Bottle** — it works and is fine for control/telemetry (autodetect toggle, manual servo jog, pump test, live annotated view); add one MJPEG route. Switching to FastAPI/Flask is optional polish, not required. WebRTC gives lower latency but adds significant complexity — defer unless latency proves to be a real problem.

### H. OpenCV De-risking & Migration

- **Does v2 still need the custom OpenCV? Largely no.** NCNN (via Ultralytics), pycoral/tflite-runtime, and picamera2 do **not** require OpenCV for inference. OpenCV was used for cv2.dnn (being dropped), resize/letterbox (replaced by picamera2 lores sizing + numpy), and box drawing (replaceable by numpy/PIL or picamera2 overlays).
- **If any OpenCV op is still wanted**, install a standard **piwheels** wheel (`pip install opencv-python-headless`) on Bullseye/aarch64 instead of the source build — headless avoids GUI deps and retires the Xenial-libjasper hack, the single most fragile component.

**Phased, non-destructive migration plan (build v2 in a separate `pi-turret-v2/` venv; leave v1 intact):**

| Phase | Goal | Validation criteria | Rollback |
|---|---|---|---|
| 0 | New v2 venv; v1 untouched | v1 still runs; v2 venv imports picamera2, tflite-runtime, ncnn | delete venv |
| **1 (milestone)** | **Fire at ANY bird, fast & reliable** — NCNN single-class bird on CPU + correct decode + closed-loop aim + non-blocking fire | hits a moving decoy ≥ target/10 trials; **no OpenCV source build**; UI shows boxes | run v1 |
| 2 | Correct Edge-TPU path (YOLOv8n INT8, 192–256 px), v8 decode, op-map verified | TPU latency ≤ CPU; accuracy ≥ CPU path on test clips | fall back to NCNN CPU path |
| 3 | UX polish: dashboard, event log/snapshots, scheduled hours | features work; no fire-reliability regression | disable features |
| 4 | Optional: re-add species discrimination + human-safety interlock | classifier mAP acceptable; human interlock blocks fire | single-class model |

**Risk matrix:**

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| v8 decode still wrong after switch | High | Low | Unit-test decode on a saved frame vs Ultralytics `model.predict` reference boxes |
| Edge-TPU ops fall to CPU at chosen input size | Med | Med | Verify compiler log; drop to 192–256 px; keep NCNN CPU as primary |
| INT8 accuracy loss on small birds | Med | Med | >300-image representative calibration; prefer larger input on CPU path |
| Servo brownout resets Pi | High | Med | Separate 5–6 V servo supply, common ground, bulk capacitor |
| Coral USB overheats on -max | Med | Low | Stay on -std unless actively cooled |
| picamera2 lores YUV420 mishandling / buffer stall | Low | Med | Use Y-plane greyscale or `YUV420_to_RGB`; always release captured requests |

**Blockers (must resolve) vs Risks (manage):** Blockers = (1) the v8 decode fix, (2) the x86-64 export/compile workflow, (3) separate servo power. Risks = TPU op-mapping, INT8 small-bird accuracy, thermal.

## New Use Cases (nice-to-have)
- **Event logging + snapshots** of fired-upon birds (timestamp, track ID, confidence, image crop) — trivial once frames/detections are in-process.
- **Detection dashboard/stats** (counts/hour, hit/miss, later species) served from the Bottle UI.
- **Scheduled active hours** (fire only during configured windows) — a state-machine guard.
- **Multi-target handling** — ByteTrack already yields multiple IDs; add a selection policy (nearest-to-center / largest / longest-dwell).
- **Deterrent escalation** — play a sound first (cheap, humane), escalate to water only if the bird persists N seconds.
- **Re-add species classification** (pigeon/crow/magpie from the Roboflow set) and a **human-safety interlock** (detect COCO "person" → inhibit fire) as Phase 4; run the human check as a cheap gate before any fire command.

## Caveats / Open Questions (resolve by on-device measurement)
- **All latency/FPS numbers must be measured on this exact Pi 4 + Coral USB.** Cited Coral figures are Pi 4B at 320/512; cited NCNN/OpenVINO/MNN figures are Pi-5-class — real Pi-4 numbers will be slower.
- **Max clean Edge-TPU input size for this model** (192 vs 256 vs 320) is empirical — confirm via the compiler op-map log and on-device accuracy on real bird clips.
- **INT8 accuracy on small/distant birds** vs the FP NCNN CPU path is unknown until measured; this drives the CPU-vs-Coral decision.
- **Servo settling/overshoot and best Kp** depend on the actual mechanism's slop/backlash — tune on-device.
- **Nozzle parallax + water drop** must be calibrated by test-firing at the real engagement distance.
- **USB 2.0 vs 3.0:** Coral throughput drops ~3× on USB 2.0 — confirm the Pi 4's blue USB 3.0 port is used.
- **Whether YOLO11n head ops map on this Coral compiler version** is uncertain; YOLOv8n is the safer Edge-TPU choice.

## Operator I/O additions (2026-06-27)

Reuse v1's existing hardware (no rewiring) and add one optional input. Wiring table + per-step
plans: `IMPLEMENTATION_PLAN.md §8` (steps 1.13–1.15).

- **LCD as a live lifecycle display (1602A, I2C bus 1, `rpi_lcd`, shares the bus with the PCA9685
  @ 0x40).** v1 only showed on/off + angles. v2 surfaces, throughout the run: boot + LAN IP, then
  per state — SEARCHING (`SCAN <spin> <fps> / trk:N ARM|SAFE`), AIMING (`AIM#id e<err> / KZ:Y WF
  ARM`), FIRING (`FIRE! #id / shots:N`), COOLDOWN, SAFE. Rendering is a low-rate thread
  (`app/display.py`) so I2C never blocks control; the device wrapper is fail-safe.
- **Indicators:** BCM23 status LED (lit while not SAFE) + BCM27 aux laser as an **opt-in** aim marker
  (default off — laser safety), via `gpiozero.LED` exactly as v1; off on disarm.
- **IR remote (PROPOSED — owner is considering it).** A simple IR receiver (owner's old Arduino kit)
  for start/stop + basic control: arm/disarm, toggle fire-enable, center, jog pan/tilt. v1 has **no
  GPIO inputs**, so this is purely additive: one receiver on a free pin (proposed **BCM17**) plus
  `dtoverlay=gpio-ir,gpio_pin=17` → the remote appears as an evdev device (rc-core); capture key
  codes with `ir-keytable -t`. Alternatives: LIRC or pigpio software decode. Seam exists
  (`app/remote.py` + `RemoteConfig`); **pin and key codes need owner confirmation + on-Pi capture.**
  An IR "e-stop"/disarm key is a cheap, useful safety affordance.