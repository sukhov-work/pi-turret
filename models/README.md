# models/ — committed Edge-TPU detector models (v2)

Tracked-in-git detector models for **tests + deployment**. The Coral runtime loads a
`*_edgetpu.tflite` from here (`config.detector.model_path`). Every finetuned run is kept (versioned)
so results stay reproducible and comparable.

All `*_edgetpu.tflite` are compiled on the **Strix** box (`edgetpu_compiler` is x86-only) from
`~/turret-ml`. A deployable file **must end `_edgetpu.tflite`** or the runtime silently falls back to CPU.

## Contents
| File | What | Classes | Input | Compile gate (edgetpu_compiler 16.0) | Notes |
|---|---|---|---|---|---|
| `yolov8n_coco80_256_int8_edgetpu.tflite` | vanilla stock YOLOv8n (COCO) | 80 | 256 | 1 subgraph · 256 ops all on TPU · 3.57 MiB on-chip · 7.88 KiB off-chip | reference + decode/golden-test fodder (bird = class 14) |
| `bird_yolov8n_256_int8_edgetpu_run1.tflite` | finetuned **single-class bird** (HUB run `test-8n-run-1`) | 1 (`bird`) | 256 | 1 subgraph · 252 ops all on TPU · 3.11 MiB on-chip · 7.88 KiB off-chip | **current first deploy candidate** |
| `bird_yolov8n_256_run1.pt` | source weights for run1 | 1 | — | — | keep to re-export / finetune |

Trained from the Roboflow set `jayson-x-an0sg/pigeons-single-class` (1152/196/63, single class `bird`).

## Conventions
- Name finetuned exports `bird_yolov8n_<imgsz>_int8_edgetpu_run<N>.tflite`; keep the matching `.pt`.
- Keep `imgsz=256` (the verified Coral deploy size) unless a new size re-passes the compile gate.
- To deploy: set `config.detector.model_path` to the chosen file, push to the Pi, measure on-device.

## Add a new finetuned model
1. Train (Ultralytics HUB cloud or local Strix) → `best.pt`.
2. On Strix: `~/turret-ml/bin/yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=~/turret-ml/datasets/pigeons-single-class/data.yaml nms=False dynamic=False`
3. Confirm the gate (`Number of Edge TPU subgraphs: 1`; file ends `_edgetpu.tflite`).
4. `rsync` the tflite + copy the `.pt` here as `run<N>`, add a row above, and log gate numbers in `DECISIONS.md`.
