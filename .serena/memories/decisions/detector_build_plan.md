# mem:decisions/detector_build_plan — the Coral detector (model) track

Step detail: `IMPLEMENTATION_PLAN.md §9`. (Related: `mem:core`, `mem:project/dev_environment`, research
`.claude/claude-docs/plans/coral-detector-selection-research.md`.)

## STATUS 2026-06-29 — ✅ DETECTOR TRACK COMPLETE (run1 live + Pi-measured)
Single-class "bird" YOLOv8n@256 INT8 is **running on the Coral on the Pi**, decode verified, latency
measured. **YOLOv8n@256 is the confirmed P1 detector (GO); MobileDet fallback NOT needed.**
- **Model:** `models/bird_yolov8n_256_int8_edgetpu_run1.tflite` (HUB run `test-8n-run-1`, single class),
  git-tracked → delivered to Pi via `git push pi main` (no rsync). `config.{py,yaml}.detector` →
  `model_path=…_run1.tflite`, `coords_normalized=true`, `num_classes=1`, `input_size_px=256`, `backend=coral_yolo`.
- **MEASURED on Pi (run1 edgetpu, USB3 SuperSpeed 5000M, `libedgetpu1-std`, system Py 3.9.2 + pycoral, no venv),
  200 iters @256:** TPU invoke **12.16 ms median (82 FPS)**; full `CoralDetector.infer`
  (preprocess+invoke+dequant+decode) **16.99 ms median (59 FPS)**; decode+dequant ≈ **4.8 ms**;
  warm `load()` ≈ 2.7 s (cold ≈ 5.2 s = TPU firmware upload). 59 FPS ≫ the 15–24 FPS control budget → **GO**.
- **Sane boxes ✓ (full-stack decode validation):** Pi edgetpu on the val frame →
  `cls=0 score=0.622 xyxy=(186.0,0.0,965.3,1098.6)`; Strix-CPU golden fixture on the same frame →
  `(186.0,0.0,965.3,1109.2) 0.622` — same box within ~10 px (TPU-vs-CPU int8 rounding).

## decode contract — PINNED 2026-06-29 (golden fixture)
- **`coords_normalized=True`** for the deployed coral_yolo path. Ultralytics v8 *detection* TFLite/edgetpu
  exports emit box xywh **normalized [0,1]** (the `.pt` emits input-pixels). The `False` path mis-decodes
  by ~1104 px (the catastrophic-decoder signature the golden test guards).
- **Real output shape is `[1,5,1344]`** (1344 = 32²+16²+8² for input 256; 8400 is for 640). `decode_v8`
  is anchor-count-agnostic — fine.
- **`decode_v8` now clips boxes to frame bounds** (matches Ultralytics `clip_boxes`); the only delta vs the
  reference was an off-frame `y1=-14`→`0` (rest matched <0.1 px). Golden test green → **178 passed / 0 skipped**.
- **Fixture generator committed:** `tests/fixtures/generate_golden_fixture.py` self-pins the flag by
  replaying the test for both values (no assumption). Regenerate for run2+ from the `_full_integer_quant.tflite`.

## coral.py fix (Pi truth, 2026-06-29)
- The edgetpu model **input tensor is INT8** (scale 1/255, zero −128); `coral.py._preprocess` fed **uint8**
  → `ValueError: Got UINT8 but expected INT8`. **Fixed:** quantize per the input tensor's own dtype +
  (scale,zero): `real=px/255 → /scale + zero → round/clip → astype(dtype)` (handles int8 + uint8 + float).
  Output dequant was already correct. Possible later opt: for scale=1/255 this reduces to `px−128`.
- Relaxed the `_edgetpu.tflite` name check to match `_edgetpu` **anywhere** (run-versioned names). pycoral
  `make_interpreter` uses the TPU regardless of filename anyway.

## Model + training strategy (decided 2026-06-29)
- **Dataset:** Roboflow `jayson-x-an0sg/pigeons-single-class`, **single class `['bird']`**, 1152/196/63,
  staged on Strix `~/turret-ml/datasets/pigeons-single-class/` (fixed absolute `path:` in data.yaml);
  doubles as the >300-img INT8 calib set. Mac source: `~/Downloads/Pigeons Single class.yolov8`.
- **imgsz=256 (train==deploy; Coral is locked at 256), epochs≈300/patience≈50.** Keep YOLOv8 aug defaults.
- **Export (Strix):** `yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=…/data.yaml nms=False dynamic=False`.
- **Next finetune = run2:** train (HUB or Strix) → export edgetpu on Strix → regenerate golden fixture →
  add `models/bird_yolov8n_256_int8_edgetpu_run2.tflite` (+ `.pt`) → re-measure on Pi. Iterate on real deployment imagery.

## Strix toolchain — INSTALLED 2026-06-29
- `edgetpu_compiler` **v16.0** (apt, global, on PATH). **`~/turret-ml`** = uv-managed **Python 3.12.13** venv
  (Strix system Py 3.13 breaks the TF chain). Tools `~/turret-ml/bin/{yolo,python}`; ultralytics 8.4.81,
  onnxruntime 1.27.0, TF ≤2.19.0, cv2 4.13.0. INT8 tflite twins live under `~/turret-ml/*_saved_model/`.

## Compile gate (Stage 0, Strix only) — YOLOv8n PASSED
`edgetpu_compiler -s` must show **`Number of Edge TPU subgraphs: 1`** + off-chip ≈0. run1: **1 subgraph,
252 ops all on TPU**, on-chip 3.11 MiB, off-chip 7.88 KiB. (Stock yolov8n@256: 1 subgraph, 256 ops.)
v8 head ops (SOFTMAX/TRANSPOSE/STRIDED_SLICE/RESHAPE) map cleanly on this toolchain.

## Truth ownership
Op-map = Strix only. ms/FPS + sane boxes = Pi only (now measured, above). Decode correctness = Mac golden
test. Pi runtime: pycoral / tflite-runtime / libedgetpu 16.0 on Bullseye; Coral on its own USB3 bus (5000M).
Reach the Pi with `ssh -o ControlMaster=no -o ControlPath=none pi` if a stale control socket times out.
