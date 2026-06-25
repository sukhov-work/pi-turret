# mem:tech_stack

Read before adding a dependency or reasoning about runtime. (Referred by `mem:core`.)

## Runtime
- On-device: **Python 3.9**, Raspberry Pi OS Bullseye (64-bit), ARM. Authoring uses newer Python on the Mac.
- No packaging file at root (no pyproject/requirements); deps installed by hand per README. v2 gets its own
  venv per machine (see `mem:project/dev_environment`). **No `uv`, no Docker, no database.**

## Libraries actually in use (v1)
- Web: `bottle` (sync WSGI); UI assets bootstrap/jquery + `index.html`.
- Vision/ML: `cv2` (OpenCV), `numpy`, **`onnxruntime`** (the real YOLO inference path, CPU on the Pi),
  `cv2.dnn` (only the SSD person check), `ultralytics` (export-time; the runtime `YOLO` import is unused/dead).
- Camera: `picamera2` + `libcamera`. Hardware: `RPi.GPIO`/`gpiozero`, `smbus` (PCA9685 @ I2C `0x40`), `rpi_lcd`.
- Streaming (v1): vendored `mjpg-streamer` with a custom `input_opencv.so`, launched via `os.system`.

## v2 direction
- Detector backend chosen by a compile gate: Coral INT8 YOLOv8n primary, CPU fallback (SSDLite-MobileDet).
  NCNN-on-CPU was the design-doc idea, but the plan standardised on the Coral/CPU split — plan wins.
  2026 Coral-detector study (`.claude/claude-docs/plans/coral-detector-selection-research.md`) recommends
  **inverting**: MobileDet@320 as the default, YOLOv8n@256 as the compile-gated upgrade (accept only on a
  clean 1-subgraph + 0 B-streaming compile); SpaghettiNet-EdgeTPU is the top upgrade candidate.
- Servo: `adafruit-circuitpython-pca9685` (v2) likely replacing the raw smbus driver.
- Streaming: USB webcam stays the default stream source (headless); picamera2 MJPEG of annotated frames is opt-in/debug.

## Gotchas
- The float/int `&` `TypeError` is **already fixed** (`PCA9685.setPWM` coerces with `int()`). Do NOT re-fix it.
- Model class order in `v1/models/v8_pigeon_best.yaml` is alphabetical: **crow=0, magpie=1, pigeon=2**.
- Fast-moving libs (Ultralytics / pycoral / picamera2 / onnx2tf) — verify current flags on the web, not from the docs.
