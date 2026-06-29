# mem:decisions/detector_build_plan — the Coral detector (model) track

The **active work** after the Mac-authored spine + UI. Step detail: `IMPLEMENTATION_PLAN.md §9`.
(Related: `mem:core`, `mem:project/dev_environment`, research
`.claude/claude-docs/plans/coral-detector-selection-research.md`.)

## STATUS 2026-06-29 — first model trained + converted + committed
First finetuned model done: HUB run `test-8n-run-1.pt` (single-class bird) → exported on Strix →
**`models/bird_yolov8n_256_int8_edgetpu_run1.tflite`** (committed; gate PASS: 1 subgraph, 252 ops all
on TPU, 3.11 MiB on-chip, 7.88 KiB off-chip). `models/` is a **committed** folder (see
`models/README.md`) holding the deployable `_edgetpu.tflite` + `.pt` source + a vanilla
`yolov8n_coco80_256_int8_edgetpu.tflite` reference. **Next: golden fixture (un-skip the decode test) →
deploy run1 to the Pi → measure latency/FPS (Pi-only truth).** Each future finetune adds a `run<N>` here.

## ⚠️ Existing models are JUNK — starting from scratch (owner, 2026-06-29)
All pre-existing weights in `v1/models/` and on the Pi (pigeon-only ONNX/tflite, `pigeon-y8s_edgetpu*`,
yolov5s, SSD-MobileNet-V3) are **obsolete — do not ship them**. Useful only as evidence that a
**YOLOv8 model already compiled cleanly to Edge-TPU here** (v1's bug was decode, not compile → de-risks
the YOLOv8n path). Train a fresh model.

## Model + training strategy (decided 2026-06-29)
- **Dataset:** owner's **Roboflow** set — **single class `['bird']`** (workspace
  `jayson-x-an0sg/pigeons-single-class`), **1152 train / 196 val / 63 test**, YOLO-format normalized
  xywh. **Staged on Strix:** `~/turret-ml/datasets/pigeons-single-class/` with a **fixed data.yaml**
  (absolute `path:` — Roboflow's default `../train/images` resolves off-tree). Source on Mac:
  `/Users/yevhens/Downloads/Pigeons Single class.yolov8`.
- **First model:** **YOLOv8n @256, single class `bird`** → output `[1,5,8400]`, matches the existing
  `decode_v8` single-class path + the golden test; `config.detector.num_classes=1` (already the default).
- **imgsz = 256, NOT 640.** The Coral inference res is the binding constraint (256 compiled clean; 320+
  untested), so **train == deploy** avoids a resolution mismatch; a 640 model's val mAP would overstate
  real 256 accuracy, and 640's small-bird benefit is lost when inferring at 256 (the fix for distant
  birds is a longer lens / later a 320 deploy + re-gate, not higher train res).
- **epochs ~300, patience ~50** (1152 imgs converge well before that; 1000/100 is overkill but would
  early-stop). Keep YOLOv8 augmentation defaults (mosaic, fliplr 0.5, hsv, close_mosaic 10).
- **Training compute:** owner leaning **Ultralytics HUB cloud** (GPU, fast) — Strix torch is CPU-only
  for now (ROCm 7.1/gfx1151 present but not wired into torch; would need `HSA_OVERRIDE_GFX_VERSION`,
  don't rabbit-hole). Local Strix CPU train OK-ish at 256. **Either way the edgetpu export+compile runs
  on Strix** (compiler is x86-local) and needs the dataset there for INT8 calib (now staged).
- **INT8 calibration:** the **Roboflow training images double as the >300-img calib set**. Export:
  `yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=<dataset>/data.yaml nms=False dynamic=False`.
- **Flow:** train (HUB or Strix) → download best.pt → export edgetpu on Strix (gate) → golden fixture →
  **deploy turret test** → collect real deployment images → annotate → more precise models.

## Strix toolchain — INSTALLED 2026-06-29
- `edgetpu_compiler` **v16.0** (apt, Coral repo, signed-by keyring) — global, on PATH.
- **`~/turret-ml`** = uv-managed **Python 3.12.13** venv (uv 0.11.25 via pipx; Strix default python is
  3.13 which breaks the TF/onnx2tf chain → 3.12 venv). Install with `uv pip install --python
  ~/turret-ml/bin/python ...`; venv has **no system pip** (`pip` pkg added so ultralytics auto-installs
  export deps). Tools: `~/turret-ml/bin/{yolo,python}`. Installed: ultralytics, roboflow, onnx, onnxslim,
  onnxruntime 1.27.0; TF auto-pulled `<=2.19.0` at export. Smoke-test log `/tmp/mlbuild.log`.

## The compile gate (Stage 0, Strix only)
`edgetpu_compiler -s` must show **`Number of Edge TPU subgraphs: 1`** and off-chip streaming ≈0. Export
with **`nms=False dynamic=False imgsz=256 int8=True data=…`**. Compiled file **must end `_edgetpu.tflite`**.
**MEASURED 2026-06-29 (stock yolov8n@256, edgetpu_compiler 16.0):** PASS — **1 subgraph, all 256 ops on
TPU (0 on CPU)**, on-chip 3.57 MiB / 3.23 MiB free, off-chip 7.88 KiB (≈0). v8 head ops
(SOFTMAX/TRANSPOSE/STRIDED_SLICE/RESHAPE) **map cleanly** → **YOLOv8n confirmed primary; MobileDet
fallback only** (if trained model's *on-Pi latency* disappoints — Pi ms/FPS still UNVERIFIED). The
single-class head is smaller than stock 80-class → expect an equal/cleaner compile.

## Integration (code already half-there)
- YOLO: `detect/coral.py::CoralDetector` + `detect/decode.py::decode_v8` (anchor-free, no objectness)
  EXIST. Set `config.detector.{backend,model_path,input_size_px=256,conf,iou,num_classes=1}`.
- **Golden fixture** (un-skips `test_v8_decode_matches_ultralytics_reference`): dump Ultralytics
  `model.predict` raw tensor → `tests/fixtures/raw_output.npy` + `predict_ref.json`, commit on Mac.
  Pins `decode_v8.coords_normalized` — permanent v5/v8 mismatch guard.

## Truth ownership
Op-map = Strix only. ms/FPS, sane boxes = Pi only (USB2 ≈ 3× slower than USB3; `libedgetpu1-std`).
Decode correctness = Mac (golden test). Pi runtime stack: pycoral 2.0.0 / tflite-runtime 2.5.0.post1 /
libedgetpu 16.0 (feranick fork for Bullseye). Coral is effectively EOL.
