# Model Iteration Runbook — pi-turret v2 detector

How to turn **field data into a better deployed detector**: collect → annotate → train → export →
golden-fixture → deploy → measure → record. This is the repeatable loop that produces each
`models/bird_yolov8n_256_int8_edgetpu_run<N>.tflite`. The first pass (run1) is documented in
`DECISIONS.md` (2026-06-29) and `mem:decisions/detector_build_plan`; this file is the reusable
procedure.

> Three machines (never confuse them): **Mac** authors code + runs pytest + git; **Strix**
> (`ssh strix`) trains/exports/`edgetpu_compiler` (x86-only); **Pi** (`ssh pi`) is the *only* source
> of latency/FPS/accuracy/aiming truth. Deploy = commit on Mac → `git push`. See `CLAUDE.md`.

## The loop

```
[Pi]  turret runs with snapshot capture on  ──► dataset/ (full + crop + meta)
[Mac] rsync snapshots off the Pi ──► annotate in Roboflow (single class 'bird')
[Strix] stage dataset ──► train (HUB or Strix) ──► export edgetpu + COMPILE GATE
[Mac] commit run<N> to models/ ──► regenerate golden fixture ──► pytest green
[Mac] set config.model_path ──► git push origin/pi/strix
[Pi]  pi_detector_bench.py ──► record ms/FPS + sane boxes
[Mac] append DECISIONS + update mem:decisions/detector_build_plan + models/README row
```

Each run is **additive and versioned** — never overwrite a previous `run<N>`; results stay comparable.

---

## 1. Collect field data (Pi)

The pipeline already captures training data via `app/snapshots.py` (gated by `config.app`):

| Config | Effect |
|---|---|
| `app.snapshot_mode` | `off` \| `every` (each detection) \| `fire_only` \| `sampled` |
| `app.snapshot_sample_every` | when `sampled`, save 1 in N frames |
| `app.snapshot_dir` | output dir (default `dataset/`) |

Each snapshot writes three files keyed `"{ms}_{trackid}"`: `_full.jpg` (whole frame), `_crop.jpg`
(the box), and `.json` (timestamp, track id, cls, score, xyxy, vx/vy, hits, predicted_xy, fired).
The crop + metadata are what make later auto-labelling cheap.

**Run a real engagement session** with `snapshot_mode=sampled` (or `every` for a short burst) so the
set reflects *deployment* imagery — the actual camera, lens, lighting, distances, and bird poses. This
is the single biggest lever on real accuracy; lab/stock images overstate it.

Pull the data to the Mac (artifact → rsync, never git):

```bash
rsync -avz -e 'ssh -o ControlMaster=no -o ControlPath=none' pi:'~/pi-turret/dataset/' ./fielddata/run<N>/
```

## 2. Annotate (Mac → Roboflow)

- Workspace/project: **`jayson-x-an0sg/pigeons-single-class`**, **single class `['bird']`** (keep it
  single-class for P1; species split is a Phase-4 change, not now).
- Upload the field `_full.jpg` frames (the `.json` boxes can seed pre-labels). Label every bird,
  including partial/edge birds (the decode clips to frame, so edge boxes are valid training signal).
- **Merge with the existing set**, don't replace it — new field data *augments* the 1152/196/63 base
  so the model keeps generalizing while learning the deployment domain.
- Export **YOLOv8 format** (normalized xywh). Roboflow's default `data.yaml` uses relative
  `../train/images` paths that resolve off-tree — you will fix this in step 3.

## 3. Stage the dataset (Strix)

```bash
# from the Mac, push the Roboflow export up (big artifact → rsync)
rsync -avz "<roboflow export>/" strix:'~/turret-ml/datasets/pigeons-single-class-run<N>/'
```

On Strix, **rewrite `data.yaml` with an absolute `path:`** (Roboflow's relative paths break training):

```yaml
path: /home/<user>/turret-ml/datasets/pigeons-single-class-run<N>
train: train/images
val: valid/images
test: test/images
names: ['bird']
```

The training images **double as the >300-image INT8 calibration set** — no separate calib set needed.

## 4. Train (HUB cloud or Strix)

Base **`yolov8n`**, **`imgsz=256`** (train == deploy; the Coral is locked at 256 — a 640 model's val
mAP overstates real 256 accuracy and its small-bird gain is lost at 256 inference), **`epochs≈300
patience≈50`** (≈1k–2k imgs converge well before 300; patience early-stops), keep YOLOv8 aug defaults
(mosaic, fliplr 0.5, hsv, close_mosaic 10).

- **HUB (owner's usual path):** train in the cloud, download `best.pt`.
- **Strix local:** `~/turret-ml/bin/yolo train model=yolov8n.pt data=<dataset>/data.yaml imgsz=256
  epochs=300 patience=50` (CPU is OK at 256; ROCm/gfx1151 exists but isn't wired into torch — don't
  rabbit-hole). Run long jobs in detached tmux: `ssh strix 'tmux new -A -d -s train "<cmd>"'`.

Either way, the **edgetpu export + compile is on Strix** (the compiler is x86-local, and the calib
dataset lives there).

## 5. Export to Edge-TPU + COMPILE GATE (Strix only)

> ⚠️ Library flags move fast (Ultralytics/onnx2tf/TF). **Verify current flags on the web at export
> time** — don't trust a pinned command blindly.

```bash
~/turret-ml/bin/yolo export model=best.pt format=edgetpu imgsz=256 int8=True \
    data=~/turret-ml/datasets/pigeons-single-class-run<N>/data.yaml nms=False dynamic=False
```

This produces `<name>_saved_model/` containing both the CPU-runnable INT8 twin
(`<name>_full_integer_quant.tflite`) and the compiled `<name>_full_integer_quant_edgetpu.tflite`.

**GATE — read the `edgetpu_compiler` log. Accept only if:**
- `Number of Edge TPU subgraphs: 1`
- Off-chip streaming ≈ 0 (a few KiB like 7.88 KiB is fine; the catastrophic case is a **split tail**
  sending most ops to CPU → Pi-4 CPU fallback ≈ 1800–2100 ms).
- Most/all ops mapped to TPU (run1: 252 ops, all on TPU; stock 80-class: 256).
- Netron: int8 in/out, output `[1, 4+nc, N]` (no objectness). For single class @256 → **`[1,5,1344]`**
  (anchors `N = (256/8)² + (256/16)² + (256/32)² = 1024+256+64 = 1344`; *not* 8400 — that's 640).

**No-Go:** any candidate needing 2+ subgraphs or a split tail. (If YOLOv8n ever splits, the MobileDet
fallback path exists — `IMPLEMENTATION_PLAN §9.5` — but run1 confirmed YOLOv8n compiles clean.)

## 6. Commit the model (Mac)

Pull both artifacts back and version them under the **committed** `models/` folder:

```bash
scp strix:'~/turret-ml/<name>_saved_model/<name>_full_integer_quant_edgetpu.tflite' \
    models/bird_yolov8n_256_int8_edgetpu_run<N>.tflite
scp strix:'~/turret-ml/<best>.pt' models/bird_yolov8n_256_run<N>.pt
```

Add a row to `models/README.md` (classes, input, gate numbers) and commit. The `.tflite` is
git-tracked, so `git push pi main` later **delivers it to the Pi — no rsync of the model needed.**

## 7. Regenerate the golden fixture (Strix → Mac)

The decode guard (`tests/test_decode.py::test_v8_decode_matches_ultralytics_reference`) must track the
new model. Regenerate it from the run's **INT8 twin** (CPU-runnable; Strix has no Coral):

```bash
# Strix: writes raw_output.npy + predict_ref.json into the repo's tests/fixtures/
cd ~/pi-turret && ~/turret-ml/bin/python tests/fixtures/generate_golden_fixture.py \
  --model ~/turret-ml/<name>_saved_model/<name>_full_integer_quant.tflite \
  --dataset ~/turret-ml/datasets/pigeons-single-class-run<N> --frame-size 1152
```

The generator **self-pins `coords_normalized`** (decodes both ways, keeps whichever matches Ultralytics'
own `predict` within 2 px). It should print `coords_normalized=True` (Ultralytics v8 tflite emits
normalized xywh). Pull + commit + test:

```bash
scp strix:'~/pi-turret/tests/fixtures/raw_output.npy'   tests/fixtures/   # see footgun: clean Strix tree
scp strix:'~/pi-turret/tests/fixtures/predict_ref.json' tests/fixtures/
.venv-v2/bin/python -m pytest tests/test_decode.py -q                      # must be green
```

If `coords_normalized` ever comes back `False`, **stop** — the export convention changed; fix
`config.detector.coords_normalized` and investigate before deploying.

## 8. Deploy (Mac)

```bash
# point config at the new model (both files)
#   config.py     detector.model_path = "models/bird_yolov8n_256_int8_edgetpu_run<N>.tflite"
#   config.yaml   detector.model_path: models/bird_yolov8n_256_int8_edgetpu_run<N>.tflite
.venv-v2/bin/python -m pytest -q          # full suite green first
git add -A && git commit -m "deploy run<N>"
git push origin main && git push pi main && git push strix main
```

## 9. Measure on the Pi (the only real numbers)

```bash
# get a real bird frame onto the Pi (artifact relay), then bench
scp -o ControlMaster=no -o ControlPath=none <bird.jpg> pi:'~/bench_bird.jpg'
ssh -o ControlMaster=no -o ControlPath=none pi \
  'cd ~/pi-turret && python3 scripts/pi_detector_bench.py --image ~/bench_bird.jpg --iters 200'
```

Record **TPU-invoke ms, full-infer ms, FPS**, and eyeball the **boxes vs the golden fixture** (same
image → ~same box confirms the full edgetpu+decode stack). run1 baseline: TPU 12.16 ms / full 16.99 ms
(59 FPS), USB3, `libedgetpu1-std`. **GO** = full infer leaves headroom over the 15–24 FPS control budget.

Also do a live check on the rig: tune `detector.conf_threshold` / `iou_threshold` on real birds (the
web UI tunes these live), and watch for false positives/misses the val mAP won't show.

## 10. Record

Append one line to `DECISIONS.md` (gate numbers + Pi ms/FPS + sane-boxes), update
`mem:decisions/detector_build_plan` with the measured numbers, and add the `models/README.md` row.

---

## Footguns (all verified on-device, run1)

| Footgun | Reality / handling |
|---|---|
| **Normalized coords** | Ultralytics v8 *detection* tflite emits xywh in **[0,1]** → `coords_normalized=True`. The `.pt` emits input-pixels. The `False` path mis-decodes by ~1100 px. |
| **INT8 input tensor** | The edgetpu input is **int8** (scale 1/255, zero −128), not uint8. `coral.py._preprocess` quantizes per the tensor's dtype — don't feed raw uint8 (raises `ValueError`). |
| **Anchor count** | `[1,5,1344]` at 256 (not 8400). `decode_v8` is anchor-count-agnostic; tests that hard-code 8400 are synthetic. |
| **`_edgetpu` naming** | Convention names runs `..._edgetpu_run<N>.tflite`. We load via **pycoral `make_interpreter`**, which uses the TPU regardless of filename; `coral.py` only checks `_edgetpu` is *present*. (The "must END `_edgetpu.tflite`" rule is an *Ultralytics-AutoBackend* concern, which we don't use on the Pi.) |
| **Frame color** | The model trained on **RGB**; `coral.py` feeds frames through unchanged. picamera2 lores YUV→Y greyscale loses color → accuracy hit. **Open:** convert to RGB in capture, or accept greyscale. Verify on-Pi. |
| **USB port** | Keep the Coral on the **USB3** bus (run1 negotiated 5000M); USB2 ≈ 3× slower. |
| **Stale SSH socket** | If `ssh pi` times out, add `-o ControlMaster=no -o ControlPath=none`. |
| **Push-to-checkout needs a clean tree** | `git push pi/strix` fails ("Could not update working tree") if the box has untracked files (e.g. fixtures the generator wrote into `~/pi-turret/tests/fixtures/`). They're identical to what you committed — `rm` them on the box, then re-push. |
| **Strix Python** | Use `~/turret-ml/bin/{yolo,python}` (Py 3.12 venv); the system Py 3.13 breaks the TF/onnx2tf export chain. |

## Per-run acceptance criteria

1. Compile gate: **1 subgraph**, off-chip ≈ 0, file name contains `_edgetpu`.
2. Golden fixture regenerated; `test_decode` real-model test **green** on the Mac; `coords_normalized=True`.
3. Pi measured: ms/FPS recorded; full infer leaves control-budget headroom (**GO**).
4. Sane boxes on a real frame (matches the fixture); live conf/iou sanity on the rig.
5. `models/` row + `DECISIONS.md` + memory updated; v1 still the rollback.

## See also
- `IMPLEMENTATION_PLAN.md §9` — the detector build track (authoritative step detail).
- `models/README.md` — model inventory + naming convention.
- `conventions/testing.md` — the golden-test contract; `conventions/hardware-safety.md` — Coral rules.
- `tests/fixtures/generate_golden_fixture.py`, `scripts/pi_detector_bench.py` — the tools.
