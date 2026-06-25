# Coral Edge-TPU Detector Selection — Research Reference (2026)

*Distilled from an external research study (mid-2026). Standalone reference for the v2 detector
decision (`IMPLEMENTATION_PLAN.md` D1 / Step 1.1). Every op-map/latency claim is toolchain- and
compiler-version-specific — re-verify on the x86 box with `edgetpu_compiler` v16.0 before trusting it.*

## Bottom line
- The working hypothesis ("YOLOv8n@256 if it compiles cleanly, else SSDLite-MobileDet@320") is
  right but should be **inverted for production**: **SSDLite-MobileDet@320 (INT8) is the safe
  default** — the only detector with a Google-published clean single-subgraph compile (124 ops on
  TPU / 1 on CPU, 0 B off-chip), full on-chip cache (4.89 / 5.12 MiB), ~9 ms class latency.
  **Single-class YOLOv8n@256 is a conditional upgrade** — adopt only if *our own* compile yields
  **1 subgraph + 0 B streaming**.
- No model decisively beats these two for this constraint set as of mid-2026.

## The decisive test (Stage 0 — do first, on the x86 box)
Run `edgetpu_compiler -s` per candidate; accept only if the report shows:
- **`Number of Edge TPU subgraphs: 1`**, and
- **`Off-chip memory used for streaming: 0.00B`**.

For YOLO exports set **`nms=False dynamic=False`** (maps far more ops to the TPU). Then benchmark
passers on the real Pi 4 + USB Coral (inference-only, `benchmark_model`). Op-map outcomes vary by
compiler / TF / Ultralytics version + flags — this re-check is the only authoritative answer.

## Why op-map is the dominant filter
- **Clean single-subgraph (SSD / anchor heads):** SSDLite-MobileDet, SSD-MobileNet-V1/V2,
  EfficientDet-Lite0–2, SpaghettiNet — the Coral model-zoo natives.
- **YOLO anchor-free heads split, and worsen each version:** v5 maps near-completely; **v8 head
  splits** (~24–26 ops to CPU: SOFTMAX / RESHAPE / TRANSPOSE / STRIDED_SLICE); v10n fails to
  quantize; v11n splits; **v26n fails** (TOPK_V2 / TILE / SPLIT / REDUCE_MAX). NanoDet / PicoDet
  disqualified (5D TRANSPOSE → CPU). Transformers (RT-DETR / D-FINE / DETR) don't compile.
- **CPU fallback on the Pi 4 is catastrophic:** a split tail → ~1800–2100 ms vs ~22 ms when mapped.
  The compiler partitions once — everything after the first unsupported op runs on CPU.

## Condensed comparison (INT8, original Coral USB)
| Model | In | Op-map | Latency | mAP / AP_small | SRAM | Verdict |
|---|---|---|---|---|---|---|
| **SSDLite-MobileDet** | 320 | clean, 1 subgraph | 9.1 ms* | 22.5 INT8 / 2.5 | on-chip | **default** |
| YOLOv8n (1-class) | 256 | risky (head splits) | ~22 ms@320 (Pi4); faster@256 | 37.3@640 float / n/a | on-chip ~3 MB | upgrade **iff 1 subgraph** |
| **SpaghettiNet-EdgeTPU L** | 320 | clean (Google NAS) | ~1.75 ms TPU-compute† | 28.0 / "+2.2% vs MobileDet" | on-chip | **top upgrade candidate** |
| EfficientDet-Lite0 | 320 | clean | 37.4 ms* | 30.4 / **5.5 (best AP_small)** | on-chip | best distant-bird accuracy if ~37 ms ok |
| YOLOv5s | 320 | 1–2 subgraphs | "sweet spot" | 26.1 / 6.4 | ~7 MB | strong YOLO option (cleaner head than v8) |
| YOLO v10n/v11n/v26n, NanoDet, PicoDet, RT-DETR | — | split / fail | poor | — | — | avoid for Coral |

\*desktop-host figures (Pi 4 over USB is slower; USB2 ≈ 3× slower than USB3).
†TPU-compute only, not USB end-to-end — one field report saw a SpaghettiNet variant at ~100 ms; verify the compile.

## Small / distant birds: resolution is the lever, not model family
INT8 AP_small @320 is poor for everything (Lite0 5.5, YOLOv5n 3.0, MobileDet 2.5, SSD-MNv2 1.2).
AP_small roughly **triples 320 → 640**, but 640 won't fit the ~8 MB SRAM / single-subgraph limit.
So the real accuracy fixes for distant birds are: **longer focal-length lens**, **frame tiling
(SAHI-style)**, or push input to **384–448 only if the compile stays single-subgraph + on-chip**.

## Upgrade order (after MobileDet ships)
1. **SpaghettiNet-EdgeTPU-M/L** (purpose-built for this chip; "+2.2% mAP at equal latency") — verify the compile.
2. **EfficientDet-Lite0@320** (best low-res AP_small) if ~37 ms fits the frame budget.
3. **YOLOv8n@256 single-class** — only if the Stage-0 gate passes.
4. **YOLOv5s@320** via the maintained zldrobit / jveitchmichaelis export.

## Toolchain reality (2026)
Google left the Coral stack unmaintained 2021–2025; the community **feranick `libedgetpu` fork**
provides working Bullseye/Bookworm runtimes. `edgetpu_compiler` v16.0 is x86-only, unmaintained but
functional. The pinned stack (**pycoral 2.0.0, tflite-runtime 2.5.0.post1, libedgetpu 16.0**) is
the known-good combo for the original USB Accelerator. Coral is effectively EOL — factor in
long-term maintenance risk.

## Caveats
- Vendor latencies (MobileDet 9.1 / Lite0 37.4 ms) are **desktop-host**; Pi 4 over USB is higher,
  USB2 ≈ 3×. YOLOv8n 22.2 ms@320 is genuinely Pi-4-measured (inference-only).
- AP_small numbers are INT8 COCO from one peer-reviewed source; real bird AP depends on our training
  data / lighting / size distribution. No primary source publishes YOLOv8n AP_small.
- MobileDet's headline 32.9% mAP is float/desktop; INT8 is ~22.5%.
- Op-map is compiler-/export-specific → the Stage-0 test on our toolchain is the only authoritative answer.

## How this informs the plan
- **`IMPLEMENTATION_PLAN.md` D1 / Step 1.1:** keep the compile-gate; consider making **MobileDet the
  default** and YOLOv8n the gated upgrade. Apply the Stage-0 acceptance criteria above.
- **`V2-design-plan.md` §A/§B:** validates "resolution is the dominant small-bird lever" and adds
  SpaghettiNet-EdgeTPU as a candidate.

*Provenance: external study `compass_artifact_wf-076dc2d1-b1ef-4e9b-b24c-4bf1ea50c87a_text_markdown.md`
(user Downloads, mid-2026). This is a distillation — consult the original for full citations.*
