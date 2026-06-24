# pi-turret v1 (Legacy) — As-Built System Reference

*Companion to the v2 redesign doc. Purpose: give a future implementation agent (Claude Code or otherwise) the complete, accurate picture of the **current** system before changing anything. Every claim here is read from the actual repo source (commit on `main`, files: `main.py`, `TurretHandler.py`, `YOLOv8.py`, `PCA9685.py`, `README.md`, `raspi_bash_history.txt`) or from the on-device command output the owner supplied. Where a fact is inferred rather than read, it is labelled. Build v2 alongside this; do not assume any part of it is correct or safe until verified on-device.*

## 1. What the system is

A Raspberry-Pi-4-based autonomous pan/tilt turret that watches a fixed area through the Pi Camera, detects birds with a YOLOv8 model, translates the target's pixel position into pan/tilt servo angles, slews to aim, and fires (originally a laser, now being converted to a water pump). It also serves a local web page on port 8001 for status, a manual servo joystick, and a live MJPEG stream from a separate USB webcam. Autodetection is off by default and toggled from the web UI; manual servo control is allowed only while autodetection is off.

The whole thing is a single Python 3.9 process plus a child `mjpg-streamer` process. There is no database, no message bus, no service-manager integration in the repo — it is run by hand with `python3 main.py`.

## 2. Repository file map

| Path | Role |
|---|---|
| `main.py` | Entry point. Bottle web server + thread orchestration + manual servo jog loop. |
| `TurretHandler.py` | Core. Camera, detection cycle, target selection, person safeguard, aiming math, fire sequence, servo/LCD/LED state, graceful exit. |
| `YOLOv8.py` | Detection engine wrapper: ONNX Runtime YOLOv8 inference + decode + NMS, **plus** a separate SSD-MobileNet-v3 classifier used only for the person check. Defines `DetectionResult`. |
| `PCA9685.py` | Customized I2C servo-driver library (16-ch PWM). Angle/pulse mapping, gradual sweep, sleep/wake. |
| `Utils.py` | Helpers imported by `YOLOv8.py`: `xywh2xyxy`, `multiclass_nms`, `draw_detections` (standard ibaiGorordo-style ONNX-YOLOv8 utilities — described by call site, internals not re-verified here). |
| `index.html`, `bootstrap.min.css`, `jquery.js`, `fonts/` | Web UI assets served statically by Bottle. |
| `models/` | `v8_pigeon_best_384_int.onnx` (active detector), `v8_pigeon_best.yaml` (class names), SSD person-model assets referenced by absolute path (see §11). |
| `edgetpu-yolo/` | Vendored copy of **bogdannedelcu/edgetpu-yolo** (a YOLOv5-oriented Edge-TPU runner). Standalone Coral experiment, **not wired into the turret**. Contains `detect.py`, `edgetpumodel.py`, `utils.py`, the `*_edgetpu.tflite` files, `data/pigeon.yaml`. |
| `mjpg-streamer/` | Vendored jacksonliam `mjpg-streamer` with a prebuilt custom `input_opencv.so` (built because `input_raspicam.so` needs `/opt/vc` headers absent on 64-bit OS). |
| `raspi_bash_history.txt` | Full shell history of how the device was set up. The authoritative record of the (brittle) environment build. |
| `__init__.py` | Package marker. |

## 3. Hardware, interfaces, and pin/address map

All GPIO uses BCM numbering via `gpiozero`. Servos are on the PCA9685, **not** the Pi's GPIO.

| Component | Interface | Address / pin | Code reference |
|---|---|---|---|
| Pi Camera Module 3 (IMX708) — detection | CSI via picamera2 | — | `Picamera2()` in `startDetectionCycle` |
| USB webcam — streaming only | USB / V4L2 | `/dev/video*` | `input_opencv.so` (mjpg-streamer) |
| Google Coral USB Accelerator | USB 3.0 | — | not used by turret; only `edgetpu-yolo/detect.py` |
| PCA9685 Servo Driver HAT | I2C bus 1 | `0x40` | `PCA9685(address=0x40)`, `smbus.SMBus(1)` |
| Pan servo (MG996R) — horizontal | PCA9685 channel | **channel 1** | `rotateGraduallyByPulse(1, …)` |
| Tilt servo (MG996R) — vertical | PCA9685 channel | **channel 0** | `rotateGraduallyByPulse(0, …)` |
| 1602A LCD | I2C | (rpi_lcd default) | `LCD()` from `rpi_lcd` |
| Aux laser (→ secondary/marker) | GPIO | **BCM 27** | `aux_laser_led = LED(27)` |
| Main laser (→ **becomes water pump** in v2) | GPIO | **BCM 26** | `main_laser_led = LED(26)` |
| Status LED | GPIO | **BCM 23** | `status_led = LED(23)` |

PWM frequency is 50 Hz (`setPWMFreq(50)`), 20 ms period, 12-bit (4096 counts).

## 4. Runtime architecture (process & threads)

`main.py` starts one process with these concurrent parts:

- **Main thread** — Bottle WSGI server. Host is the machine's own LAN IP (discovered by opening a UDP socket to `8.8.8.8` and reading the local socket name), port `8001`.
- **`streamingThread`** (daemon) — runs `mjpg-streamer` via `os.system(...)`, blocking that thread for the process lifetime. Serves the USB-webcam MJPEG.
- **`turretThread`** (`threading.Timer`, 0.02 s, daemon) — calls `turret_handler.startDetectionCycle()`, which is an **infinite blocking `while True` loop**. This is the detection/fire engine.
- **`controlThread`** (`threading.Timer`, 1 s, daemon) — calls `turretControlfunc`, which **reschedules itself every 0.05 s** via a new `threading.Timer`. This is the manual servo jog loop.

Cleanup: `atexit` + `SIGTERM`/`SIGINT` handlers call `turret_handler.gracefulExit()`, which re-centers the turret, sleeps the PCA9685, turns off all LEDs, clears the LCD, and destroys CV windows.

**Concurrency hazard:** both the detection loop (auto aiming/fire) and the jog loop (manual) drive the same servos through the same `TurretHandler`. `turretControlfunc` guards against this by short-circuiting (zeroing steps, sleeping 5 s) when autodetection is enabled, so in practice only one mover is active at a time — but there is no lock; the safety is purely the mode check.

## 5. Detection pipeline (per frame, inside `startDetectionCycle`)

1. **Capture.** `picam2.capture_array()` from a preview config: `main={"format":"RGB888","size":(1152,1152)}`, continuous autofocus (`AfModeEnum.Continuous`). 1152 is chosen as a square multiple of 384 (the model input).
2. **Detect.** `getObjects(frame, targets=['pigeon','crow','magpie'])` → calls the `YOLOv8` detector (`self.yolov8_detector(frame)`).
   - Inside `YOLOv8.detect_objects`: BGR→RGB, resize to model input (384×384), scale to 0–1, HWC→CHW, add batch dim, float32.
   - **Inference is ONNX Runtime** (`onnxruntime.InferenceSession(..., providers=get_available_providers())`) — *not* OpenCV DNN. On the Pi this resolves to the CPU provider.
   - **Decode is correct YOLOv8** (anchor-free): `predictions = squeeze(output[0]).T`; `scores = max(predictions[:,4:], axis=1)`; threshold; `class_ids = argmax(predictions[:,4:])`; boxes = `predictions[:,:4]` rescaled to image size and `xywh2xyxy`; then `multiclass_nms`. Confidence threshold **0.7**, IoU **0.5**.
3. **Target selection.** `getTargetPigeonCandidateBox(0.7, results)`:
   - If **any** `crow` or `magpie` is in the frame → **inhibit** (return no target). Crows/magpies act as a do-not-fire signal.
   - Else sort detections by score desc; if the top one (a pigeon) scores > 0.7, return its box.
   - Net behavior: **v1 fires only at pigeons, and only when no crow/magpie is present.**
4. **Confirmation counter.** A candidate increments `detection_confidence_counter`; firing requires it to reach `detection_confidence_threshold = 2` (two qualifying frames). If fewer than 2 qualifying frames occur within `detection_reset_threshold = 30` frames, all counters reset (false-positive damping).
5. **Person safeguard.** Once the counter hits threshold, `isPersonInCurrentFrame` runs the **separate SSD-MobileNet-v3-Large COCO** model (via `cv2.dnn_DetectionModel`, 384×384, mean 127.5, scale 1/127.5, swapRB) and looks for class `person`. **Bug/contradiction:** the threshold is set to `0.9`, but the inline comment says it "should not be higher than 0.3 for safety." At 0.9 the safeguard almost never trips, so v1's human protection is effectively very weak. (v2 drops this safeguard for phase 1 anyway, but the contradiction should be understood, not copied.)
6. **Fire.** If no person, call `pointAndFire(box)` (see §6), reset the counter.

OpenCV's role in v1, precisely: (a) the SSD person classifier via `cv2.dnn`, (b) colour-convert/resize/`imshow` debug, (c) box drawing helpers. The custom OpenCV 4.4.0 source build is therefore still a dependency, but **not** for the main YOLO inference.

## 6. Targeting and servo math

**Angle ↔ pulse (microseconds), defined in `PCA9685.setRotationAngle` and mirrored in `TurretHandler`:**
`pulse_us = angle_deg × (2000 / 180) + 501`  ≈ `angle × 11.111 + 501`; inverse `angle = (pulse − 501) / 11.111`.
`setServoPulse` converts µs→counts: `counts = pulse_us × 4096 / 20000`.

**Mechanical envelope (clamped in both files):**
- Pan (channel 1): **5°–47°** (≈42° range), pulse ≈ 556–1024; `main.py` clamps pulse 560–1040.
- Tilt (channel 0): **5°–25°** (≈20° usable, code start_y=0), pulse ≈ 556–779; `main.py` clamps 560–780.
- Home/base: pan 31° (~pulse 850), tilt 23° (~pulse 760). Persisted to `last_servo_state.txt` (two ints) so position survives restarts.

**Pixel→angle mapping in `pointAndFire` (camera 1152×1152, center 576):**
- `angle_x = −((box_cx − 576) / 25) + 31 + 5`  (coefficient **25**, sign negative for correct orientation)
- `angle_y = ((box_cy − 576) / 15) + 23 + 5`  (coefficient **15**), with a lower-frame correction: if `angle_y > 18`, `angle_y = 18 + (angle_y − 25)//3`.
- If either result is outside [5–47, 5–25], the shot is skipped.

These coefficients are hand-tuned, single-shot, **open-loop** — there is no feedback after the move and no model of nozzle/camera parallax or water-jet drop. This is the core aiming weakness v2 replaces with closed-loop visual servoing + calibration.

**Two different move primitives — know which is which:**
- `rotateGradually(x,y,prev)` → `PCA9685.setRotationAngleGradually`: steps in **±5-pulse** increments with **30 ms** sleep per step. A full pan sweep (556→1024) is ~93 steps ≈ **2.8 s**; full tilt ~1.7 s. Used by `setTurretState`, `pointAndFire`, and `gracefulExit`. *This is the slow path and a major latency source during auto-fire.*
- `rotateGraduallyByPulse(channel,pulse)` → `setServoPulse` directly (a **single jump**, despite the name). Used by the manual jog loop with 15-pulse steps every 50 ms.

`rotateGradually` also toggles the chip: `start_PCA9685()` (MODE2=0x04) before, `exit_PCA9685()` (MODE2=0x00) after — i.e. the outputs are re-enabled/idled per move. v2 should initialize once and stop toggling.

## 7. Fire sequence and state

In v1, "fire" = `triggerAuxLaser()` then `triggerMainLaser()`:
- `triggerAuxLaser(2.0)`: aux LED (BCM 27) on for 2 s, off, 0.5 s settle.
- `triggerMainLaser(2.0)`: LCD "Triggered main", main LED (BCM 26) on for 2 s, off, 0.5 s settle.

Both use blocking `time.sleep`, so during a fire the detection loop is stalled ~5 s (plus the up-to-2.8 s gradual slew before it). Nothing tracks during this window. **In v2, BCM 26 drives the water pump (through a relay/MOSFET, not directly), and the fire action must be non-blocking** (timer-based pump on/off inside a state machine).

State machine in v1 is implicit: a `while True` with counters, not an explicit SEARCHING/TRACKING/FIRING/COOLDOWN model. The LCD shows live status: line 1 = detection state / counter, line 2 = current pan/tilt angles or computed move.

## 8. Web / control interface (Bottle, port 8001)

| Method / route | Action |
|---|---|
| `GET /` | Renders `index.html` (status + stream + controls). |
| `GET /api/turret-state` | Returns `{state: Enabled\|Disabled}` (autodetect on/off). |
| `POST /api/cmd` | Body one of: `enable_turret`, `disable_turret`, `enable_aux_laser`, `disable_aux_laser`. |
| `POST /api/control-cmd` | Manual jog: `up`/`down`/`left`/`right`/`stop` (sets `VStep`/`HStep` = ±15 / 0). |
| `GET /<filename>`, `GET /fonts/<filename>` | Static assets. |

The jog commands only move the turret when autodetection is **off** (the control loop no-ops otherwise). The live video on the page is the **USB-webcam** mjpg-streamer feed, served on mjpg-streamer's own HTTP port — it is **not** the annotated detection frames from the Pi Camera. (v2 proposal: stream the annotated Pi-Camera frames instead.)

## 9. Streaming subsystem

`mjpg-streamer` is launched from `streamingFunc` via `os.system`, using `input_opencv.so` (custom build) for input and `output_http.so` (`-w www`) for output. The native `input_raspicam.so` plugin was abandoned because it needs `/opt/vc/include` headers that don't exist on 64-bit Raspberry Pi OS (documented at length in the bash history and legacy log). This subsystem is dated and fragile; v2 replaces it with picamera2's built-in MJPEG encoder serving annotated frames.

## 10. The Coral side-experiment (important context for v2)

The Coral path was **only ever tested standalone**, never integrated into the turret. Test command from history:
`python3 detect.py -m pigeon-y8s_edgetpu384.tflite --names data/pigeon.yaml --conf_thresh 0.5 --stream`
run inside the vendored **bogdannedelcu/edgetpu-yolo** repo. That repo's decode/NMS is written for **YOLOv5** output (anchor-based, `[1,25200,85]`, objectness channel), but `pigeon-y8s_edgetpu384.tflite` is a **YOLOv8** model (anchor-free, `[1,4+nc,8400]`, no objectness, transposed layout). The decoder/model mismatch is the prime suspect for the "horrible accuracy," compounded by an imperfect INT8 quantization and an Edge-TPU compile that was attempted on the Pi (the compiler is x86-64 only and cannot run there). Driver level works: `libedgetpu1-std 16.0`, `python3-pycoral 2.0.0`, `python3-tflite-runtime 2.5.0.post1` are installed and the Coral bird-classification example ran. See the v2 doc for the corrected pipeline.

## 11. Model assets and training lineage

- **Active detector:** `models/v8_pigeon_best_384_int.onnx` — custom YOLOv8 trained on a Roboflow dataset with classes **`['crow','pigeon','magpie']`** (names from `models/v8_pigeon_best.yaml`), exported to ONNX and INT-quantized at 384×384. Dataset: `universe.roboflow.com/jayson-x-an0sg/pigeons-h30dy`. Training notebook: Ultralytics YOLOv8 on Colab.
- **Person classifier:** SSD-MobileNet-v3-Large COCO (`frozen_inference_graph.pb`, `ssd_mobilenet_v3_large_coco_2020_01_14.pbtxt`, `coco.names`), loaded from the **hard-coded absolute path** `'/home/jayson/opencv_test_detection/YoloRunner/models/ssd/'`. **Portability footgun:** this path is baked into `TurretHandler.__init__`; on any other layout the detector init will fail. v2 phase 1 drops this model.
- **Coral model:** `pigeon-y8s_edgetpu384.tflite` (and 192/640 variants) in `edgetpu-yolo/`.

## 12. Environment & build (the brittle parts)

Confirmed on-device: **Debian 11 Bullseye, 64-bit (aarch64), Python 3.9.2**, `libcamera-apps` build 2023-07-17 / `libcamera` v0.0.5, picamera2 in use.

Fragility ranked, from the bash history:
1. **OpenCV 4.4.0 compiled from source**, with the Ubuntu **Xenial** security repo added to get `libjasper-dev`, and `WITH_JASPER` handling. This is the single most fragile component and the hardest to reproduce. v2 aims to remove the dependence (NCNN/pycoral/picamera2+numpy don't need it) or replace it with a piwheels `opencv-python-headless` wheel.
2. **mjpg-streamer custom `input_opencv.so`** (built after the `/opt/vc` raspicam dead-end, including a `firmware` repo `vc` copy that was later removed).
3. **On-device Edge-TPU export toolchain** attempts: pinned `onnx2tf==1.17.5`, `onnxsim==0.4.33`, `setuptools` downgrade churn, CMake 3.25 built from source — all to try (and fail) to run the x86-only `edgetpu_compiler` on the Pi.
4. `python3-pycoral`/`libedgetpu1-std` installed via the deprecated `apt-key add` flow (Bullseye-era; would differ on Bookworm).

Coral runtime uses `libedgetpu1-std` (throttled clock), not `-max`.

## 13. Known bugs, gotchas, and footguns

1. **Import-time execution (critical).** `TurretHandler.py` ends with un-guarded module-level code: `h = TurretHandler(); h.setTurretState(True); h.startDetectionCycle()`. Because `main.py` does `from TurretHandler import TurretHandler`, importing the module **instantiates hardware and enters the infinite detection loop before `main.py`'s own code runs**. Either this trailing block must be wrapped in `if __name__ == '__main__':` (it's a leftover standalone test harness) or `main.py` is currently broken by it. A future agent must resolve this first. *(Read directly from source.)*
2. **Weak person safeguard.** Threshold 0.9 vs the code's own "should be ≤0.3" comment (see §5).
3. **Hard-coded absolute SSD path** (see §11).
4. **Blocking fire + slow gradual slew** stall detection ~5–8 s per engagement; no tracking during/after (see §6–7).
5. **PCA9685 enabled/idled per move** via MODE2 toggling — adds latency/jitter.
6. **`rotateGraduallyByPulse` is not gradual** — the name misleads; it's a single `setServoPulse`. Don't assume smoothing where there is none.
7. **Float→int already handled.** The legacy log's `TypeError: unsupported operand & between float and int` is from the stock Waveshare driver; the repo's `PCA9685.setPWM` already coerces with `int()`. Don't "fix" a bug that's gone.
8. **No locking** between the two servo movers; safety relies on the autodetect mode check only (see §4).
9. **Two cameras, two roles:** Pi Camera = detection (not streamed); USB webcam = stream (not used for detection). Easy to conflate.
10. **Tilt usable range is tiny** (~4 effective degrees by the code's own comment), with a special lower-frame correction in `pointAndFire`. Aiming vertically is inherently constrained by the mechanism.

## 14. Magic-number glossary

| Value | Meaning | Where |
|---|---|---|
| `0.7` | YOLO confidence threshold (detect + target select) | `YOLOv8` ctor, `getTargetPigeonCandidateBox` |
| `0.5` | YOLO NMS IoU threshold | `YOLOv8` ctor |
| `0.9` | Person-class threshold (too high; see bug #2) | `startDetectionCycle` |
| `2` | Confirming frames required before firing | `detection_confidence_threshold` |
| `30` | Frames before counters reset | `detection_reset_threshold` |
| `1152` | Camera capture W=H (3×384) | `camera_width/height` |
| `384` | Model input W=H | ONNX model / SSD input |
| `25`, `15` | Pixel→angle divisors (pan, tilt) | `pointAndFire` |
| `2000/180`, `+501` | Angle→pulse-µs slope and offset | `PCA9685`, `TurretHandler` |
| `±5`, `0.03 s` | Gradual-sweep step and per-step delay | `setRotationAngleGradually` |
| `±15`, `0.05 s` | Manual-jog step and loop period | `turretControlfunc` |
| `50` Hz | Servo PWM frequency | `setPWMFreq` |
| `0x40`, bus `1` | PCA9685 I2C address/bus | `PCA9685` ctor |
| `8001` | Web server port | `main.py` |
| `26`/`27`/`23` | Main(→pump)/Aux/Status GPIO (BCM) | `TurretHandler` ctor |
| `1`/`0` | Pan / Tilt PCA9685 channels | throughout |

## 15. End-to-end data flow (one line)

Pi Camera → picamera2 1152² RGB → resize 384² → ONNX Runtime YOLOv8 (CPU) → decode+NMS (conf 0.7) → target = top pigeon, inhibited by any crow/magpie → 2-frame confirm → SSD person check (0.9) → `pointAndFire`: pixel→angle (÷25, ÷15) → `rotateGradually` (±5/30 ms slew, up to ~2.8 s) → aux+main LED 2 s each (→ becomes pump) → reset → repeat. Web UI (Bottle :8001) toggles autodetect and jogs servos when off; USB webcam streams separately via mjpg-streamer. Coral path exists only as an unintegrated, mis-decoded standalone experiment.
