# mem:tech_stack

Read before adding a dependency or reasoning about runtime. (Referred by `mem:core`.)

## Runtime
- On-device: **Python 3.9**, Raspberry Pi OS Bullseye (64-bit), ARM. Authoring uses newer Python on the Mac.
- No packaging file at root (no pyproject/requirements); deps installed by hand per README. v2 gets its own
  venv per machine (Mac: `.venv-v2`, Py 3.9.6). The app uses **no database**. (Strix's *export* toolchain
  does use uv + has Docker — see `mem:decisions/detector_build_plan` — but that's the build box, not the app.)

## Libraries actually in use (v1)
- Web: `bottle` (sync WSGI); UI assets bootstrap/jquery + `index.html`.
- Vision/ML: `cv2` (OpenCV), `numpy`, **`onnxruntime`** (the real YOLO inference path, CPU on the Pi),
  `cv2.dnn` (only the SSD person check), `ultralytics` (export-time; the runtime `YOLO` import is unused/dead).
- Camera: `picamera2` + `libcamera`. Hardware: `RPi.GPIO`/`gpiozero`, `smbus` (PCA9685 @ I2C `0x40`), `rpi_lcd`.
- Streaming (v1): vendored `mjpg-streamer` with a custom `input_opencv.so`, launched via `os.system`.

## v2 direction / as-built
- **Detector: RESOLVED 2026-06-29 — single-class YOLOv8n@256 INT8 on the Coral is the P1 detector**
  (gate passed: 1 subgraph, all ops on TPU; on-Pi **16.99 ms / 59 FPS**). The earlier 2026 research bias
  toward a **MobileDet@320 default was overridden** by the measured op-map + latency. SSDLite-MobileDet@320
  = documented **fallback only** (same `Detection` contract, `detect/mobiledet_coral.py` NOT built — not
  needed). NCNN-on-CPU was a design-doc idea the plan dropped for the Coral path.
- **Pi runtime detector stack (deps, no venv — system Py 3.9 + pycoral):** `pycoral` / `tflite_runtime` /
  `libedgetpu 16.0` (feranick fork, Bullseye), `cv2` 4.4.0, `numpy` 1.26.0. Coral on its own **USB3** bus (5000M).
- **Coral I/O (verified):** input tensor **int8** (scale 1/255, zero −128) — `coral.py._preprocess` quantizes
  per the input dtype (NOT raw uint8); output **int8 → normalized [0,1] xywh** → `decode_v8(coords_normalized=True)`.
- **Servo driver: v2 PORTED v1's raw `smbus` PCA9685 driver** (`actuate/pca9685.py`, init-once, keeps `int()`
  coercion) — did **NOT** adopt `adafruit-circuitpython-pca9685`. `ServoController` clamps angle+pulse.
- Streaming: USB webcam is the default stream source via a separate **mjpg-streamer subprocess**
  (`app/streamer.py`); picamera2 MJPEG of annotated frames is opt-in/debug. Web stays `bottle` (lazy import).

## Gotchas
- The float/int `&` `TypeError` is **already fixed** (`PCA9685.setPWM` coerces with `int()`). Do NOT re-fix it.
- Model class order in `v1/models/v8_pigeon_best.yaml` is alphabetical: **crow=0, magpie=1, pigeon=2** (v1 only;
  v2 is single-class `['bird']`).
- Fast-moving libs (Ultralytics / pycoral / picamera2 / onnx2tf) — verify current flags on the web, not from the docs.
