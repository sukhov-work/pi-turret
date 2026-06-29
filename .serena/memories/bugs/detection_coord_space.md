# mem:bugs/detection_coord_space — detections must land in FULL-FRAME px, not lores px

Found 2026-06-29 on the first on-Pi run (tracks clustered top-left, aim_error ~742px,
pan/tilt pinned to clamps). Durable invariant; re-read `detect/coral.py` to trust specifics.

## The invariant
The whole aim chain works in **capture full-frame pixel space** = `camera.capture_width_px`
(1152), NOT the lores detection size (`detector.input_size_px`, 256):
- `killzone.cx_px=576` (=1152/2), `strategy.score_track(..., frame_w=cfg.camera.capture_width_px)`
  (`app/control.py`), the web tactical canvas (`app/web.py` `frame={w,h}=capture_*`), and the
  fitted calibration all assume 1152.
- `detect/decode.py::decode_v8` maps model-input px → full-frame px via
  `sx = frame_width_px / input_size_px`. Pass the **full frame size (1152)** as
  `frame_width_px`, never the lores frame's own shape (256), or `sx=1` and detections stay in
  256-space → everything downstream is wrong (clustering, huge aim error, clamp saturation).

## The fix (as-built)
`CoralDetector(cfg.detector, frame_width_px=cfg.camera.capture_width_px,
frame_height_px=cfg.camera.capture_height_px)` (wired in `main.py`). `_frame_dims(frame)` returns
the configured dims, else falls back to `frame.shape` (bench/no-camera). Guard test:
`tests/test_coral_detector.py`. The pipeline still feeds the detector the **256 lores** frame
(efficiency); the detector just rescales its output to 1152.

## If it regresses
Symptom = tracks bunch in one screen corner + aim_error in the hundreds of px + pan/tilt sit at the
clamp limits. Check that `CoralDetector` got the camera capture dims, not the lores size.
Related: `mem:core`, `mem:architecture/v2_scaffold`.
