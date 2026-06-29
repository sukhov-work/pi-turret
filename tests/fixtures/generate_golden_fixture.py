#!/usr/bin/env python3
"""Capture the decode golden fixture from an Ultralytics TFLite model (run on Strix).

Produces the two files consumed by
``tests/test_decode.py::test_v8_decode_matches_ultralytics_reference``:

  * ``raw_output.npy``  - the dequantized raw model output tensor (``[1,4+nc,8400]`` or
    ``[1,8400,4+nc]``) EXACTLY as ``detect/coral.py`` reads it from the Edge-TPU interpreter
    on the Pi: the literal tflite output, dequantized, BEFORE any coordinate scaling.
  * ``predict_ref.json`` - Ultralytics' own decode of the SAME model on the SAME frame
    (boxes in full-frame pixels) plus the params the test must replay.

Why a TFLite model and not the ``.pt``: Ultralytics' v8 *detection* TFLite/edgetpu exports emit
box xywh in NORMALIZED ``[0,1]`` coords, while the ``.pt`` emits input-pixel coords. ``coral.py``
feeds the raw tflite output straight into ``decode_v8``, so the deployed path needs
``coords_normalized=True``. This script PINS that EMPIRICALLY: it decodes the captured tensor both
ways and bakes in whichever reproduces Ultralytics' reference within 2 px -- no assumptions.

Strix has no Coral, so pass the *plain INT8* tflite (CPU-runnable), not the ``_edgetpu`` one. Its
I/O contract (shape, quant params, normalized coords) is identical to the compiled edgetpu twin.

Run from the repo root so ``from detect.decode import decode_v8`` resolves:

    cd ~/pi-turret && ~/turret-ml/bin/python tests/fixtures/generate_golden_fixture.py \\
        --model <int8 .tflite> [--image <frame.jpg>] [--frame-size 1152]

Then scp ``raw_output.npy`` + ``predict_ref.json`` back to the Mac and commit them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def find_val_image(dataset_dir: str):
    """First image under a val/test/train split of a YOLO dataset dir."""
    for sub in ("valid/images", "val/images", "test/images", "train/images"):
        d = os.path.join(dataset_dir, sub)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(IMG_EXTS):
                    return os.path.join(d, f)
    return None


def list_val_images(dataset_dir: str, limit: int = 40):
    """Up to `limit` candidate images (val first, then test) for clean-frame search."""
    out = []
    for sub in ("valid/images", "val/images", "test/images"):
        d = os.path.join(dataset_dir, sub)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(IMG_EXTS):
                    out.append(os.path.join(d, f))
    return out[:limit]


def get_interpreter(autobackend):
    """Locate the tflite Interpreter on an Ultralytics AutoBackend (robust to attr renames)."""
    for name in ("interpreter", "model", "net"):
        obj = getattr(autobackend, name, None)
        if obj is not None and hasattr(obj, "get_output_details"):
            return obj
    for obj in vars(autobackend).values():
        if hasattr(obj, "get_output_details") and hasattr(obj, "get_tensor"):
            return obj
    raise RuntimeError("could not locate the tflite Interpreter on the AutoBackend")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="plain INT8 .tflite (NOT _edgetpu)")
    ap.add_argument("--image", default=None, help="frame to run on; default = scan the dataset")
    ap.add_argument("--dataset",
                    default=os.path.expanduser("~/turret-ml/datasets/pigeons-single-class"))
    ap.add_argument("--imgsz", type=int, default=256)
    ap.add_argument("--frame-size", type=int, default=1152,
                    help="square frame the boxes map back to (letterbox == squash when square)")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--max-boxes", type=int, default=5,
                    help="prefer a clean frame with <= this many detections")
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    import cv2  # noqa: WPS433 (lazy: heavy deps live in the Strix venv)
    from ultralytics import YOLO

    sys.path.insert(0, os.getcwd())
    from detect.decode import decode_v8  # the repo decode under test

    fs = args.frame_size
    model = YOLO(args.model, task="detect")

    # Choose a frame: prefer one with 1..max_boxes confident detections at the real conf so
    # decode_v8's NMS and Ultralytics' NMS agree on count (the test asserts equal counts).
    if args.image:
        candidates = [args.image]
    else:
        candidates = list_val_images(args.dataset)
        if not candidates:
            one = find_val_image(args.dataset)
            candidates = [one] if one else []
    if not candidates:
        raise SystemExit(f"no candidate images (give --image or check --dataset {args.dataset})")

    chosen_img = None
    chosen_frame = None
    chosen_res = None
    fallback = None  # (n, img, frame, res) best-effort if nothing in the clean range
    for path in candidates:
        img_bgr = cv2.imread(path)
        if img_bgr is None:
            continue
        frame_bgr = cv2.resize(img_bgr, (fs, fs))
        res = model.predict(source=frame_bgr, imgsz=args.imgsz, conf=args.conf,
                            iou=args.iou, verbose=False)[0]
        n = len(res.boxes)
        if fallback is None or (n > 0 and n < fallback[0]):
            if n > 0:
                fallback = (n, path, frame_bgr, res)
        if 1 <= n <= args.max_boxes:
            chosen_img, chosen_frame, chosen_res = path, frame_bgr, res
            break

    if chosen_res is None:
        if fallback is None:
            raise SystemExit("no image produced any detection at conf=%.2f" % args.conf)
        _, chosen_img, chosen_frame, chosen_res = fallback
        print("WARN: no frame with 1..%d boxes; using busiest-clean fallback %s"
              % (args.max_boxes, os.path.basename(chosen_img)))

    res = chosen_res
    nbox = len(res.boxes)
    print("frame=%s detections=%d (conf=%.2f iou=%.2f)"
          % (os.path.basename(chosen_img), nbox, args.conf, args.iou))

    # Reference: Ultralytics' own decode, boxes in full-frame px, sorted by score desc
    # (the test zips sorted(dets, -score) against this list in order).
    ref_xyxy = res.boxes.xyxy.cpu().numpy().astype(float)
    ref_conf = res.boxes.conf.cpu().numpy().astype(float)
    ref_cls = res.boxes.cls.cpu().numpy().astype(int)
    order = np.argsort(-ref_conf)
    ref_xyxy, ref_conf, ref_cls = ref_xyxy[order], ref_conf[order], ref_cls[order]

    # Raw literal tflite output (== what coral.py reads), straight from the interpreter the
    # last predict() invoked, BEFORE AutoBackend's coordinate scaling.
    interp = get_interpreter(model.predictor.model)
    det = max(interp.get_output_details(), key=lambda d: int(np.prod(d["shape"])))
    raw_q = interp.get_tensor(det["index"])
    scale, zero = det["quantization"]
    raw = raw_q.astype(np.float32)
    if scale:  # dequantize INT8/UINT8 -> float, identical to coral.py
        raw = (raw - float(zero)) * float(scale)
    print("raw output: shape=%s dtype=%s quant(scale=%s zero=%s)"
          % (tuple(raw.shape), raw_q.dtype, scale, zero))

    # Self-pin coords_normalized: replay the TEST's exact comparison for both flags.
    def evaluate(coords_normalized):
        dets = decode_v8(raw, input_size_px=args.imgsz, frame_width_px=fs,
                         frame_height_px=fs, conf_threshold=args.conf,
                         iou_threshold=args.iou, coords_normalized=coords_normalized)
        dets = sorted(dets, key=lambda d: -d.score)
        if len(dets) != nbox:
            return len(dets), False, float("inf")
        cls_ok = all(int(d.cls_id) == int(ref_cls[i]) for i, d in enumerate(dets))
        max_err = 0.0
        for i, d in enumerate(dets):
            max_err = max(max_err, float(np.max(np.abs(np.array(d.xyxy) - ref_xyxy[i]))))
        return len(dets), cls_ok, max_err

    results = {}
    for cn in (True, False):
        n, cls_ok, err = evaluate(cn)
        results[cn] = (n, cls_ok, err)
        print("coords_normalized=%-5s -> n=%d cls_ok=%s max_corner_err_px=%.3f"
              % (cn, n, cls_ok, err))

    chosen = None
    for cn in (True, False):  # prefer True (the expected tflite convention) on a tie
        n, cls_ok, err = results[cn]
        if n == nbox and cls_ok and err <= 2.0:
            chosen = cn
            break
    if chosen is None:
        chosen = min((True, False), key=lambda cn: results[cn][2])
        print("WARNING: neither flag matched within 2px; choosing coords_normalized=%s by min "
              "error (%.3f). Inspect before committing!" % (chosen, results[chosen][2]))

    boxes = [{"cls_id": int(ref_cls[i]), "score": float(ref_conf[i]),
              "xyxy": [float(v) for v in ref_xyxy[i]]} for i in range(nbox)]
    ref = {
        "input_size_px": int(args.imgsz),
        "frame_width_px": int(fs),
        "frame_height_px": int(fs),
        "conf": float(args.conf),
        "iou": float(args.iou),
        "coords_normalized": bool(chosen),
        "model": os.path.basename(args.model),
        "image": os.path.basename(chosen_img),
        "raw_shape": [int(x) for x in raw.shape],
        "boxes": boxes,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    raw_path = os.path.join(args.out_dir, "raw_output.npy")
    ref_path = os.path.join(args.out_dir, "predict_ref.json")
    np.save(raw_path, raw.astype(np.float32))
    with open(ref_path, "w") as fh:
        json.dump(ref, fh, indent=2)

    print("\nWROTE:")
    print("  %s  (shape=%s)" % (raw_path, tuple(raw.shape)))
    print("  %s  (coords_normalized=%s, n_boxes=%d)" % (ref_path, chosen, nbox))
    print("scp both back to the Mac repo tests/fixtures/, then run pytest.")


if __name__ == "__main__":
    main()
