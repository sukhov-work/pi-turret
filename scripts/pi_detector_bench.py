#!/usr/bin/env python3
"""On-Pi inference benchmark + sanity for the Coral YOLOv8 detector (Pi-only truth).

Reports two latencies -- bare TPU invoke (set_tensor + invoke + get_tensor) and full
``CoralDetector.infer`` (adds dequant + decode_v8 + NMS) -- plus the detections on a real
frame so the boxes can be eyeballed against the golden fixture. Run on the Pi:

    cd ~/pi-turret && python3 scripts/pi_detector_bench.py --image <bird.jpg> --iters 200

Latency is input-independent (TPU time), so the timing loop uses a frame already sized to
the model input (no resize inside the loop). ``--image`` drives the sanity boxes; the model
was trained on RGB, so the image is converted BGR->RGB to match how the fixture was captured.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", default=None, help="real frame for the sanity boxes")
    ap.add_argument("--frame-size", type=int, default=1152, help="full-frame px the boxes map to")
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--model", default=None, help="override config detector.model_path")
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from config import load_config
    from detect.coral import CoralDetector

    cfg = load_config()
    if args.model:
        cfg.detector.model_path = args.model
    d = cfg.detector
    print("model=%s" % d.model_path)
    print("input=%d num_classes=%d conf=%.2f iou=%.2f coords_normalized=%s"
          % (d.input_size_px, d.num_classes, d.conf_threshold, d.iou_threshold, d.coords_normalized))

    detector = CoralDetector(d)
    t0 = time.perf_counter()
    detector.load()
    print("load(): %.1f ms" % ((time.perf_counter() - t0) * 1e3))
    _in = detector._interpreter.get_input_details()[0]
    _out = detector._interpreter.get_output_details()[0]
    print("input  dtype=%s shape=%s quant=%s" % (_in["dtype"].__name__, list(_in["shape"]), _in["quantization"]))
    print("output dtype=%s shape=%s quant=%s" % (_out["dtype"].__name__, list(_out["shape"]), _out["quantization"]))

    import cv2
    fs = args.frame_size
    isz = d.input_size_px
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit("cannot read %s" % args.image)
        sanity_frame = cv2.cvtColor(cv2.resize(img, (fs, fs)), cv2.COLOR_BGR2RGB)
    else:
        sanity_frame = np.random.randint(0, 255, (fs, fs, 3), dtype=np.uint8)

    # --- Sanity: detections on the real frame (full pipeline) ---
    dets = detector.infer(sanity_frame)
    label = os.path.basename(args.image) if args.image else "synthetic"
    print("\n=== detections (n=%d) on %s ===" % (len(dets), label))
    for det in sorted(dets, key=lambda x: -x.score)[:10]:
        x1, y1, x2, y2 = det.xyxy
        print("  cls=%d score=%.3f xyxy=(%.1f,%.1f,%.1f,%.1f) center=(%.1f,%.1f)"
              % (det.cls_id, det.score, x1, y1, x2, y2, det.cx, det.cy))

    # --- Latency: a frame already at model input so the timing excludes resize ---
    bench_frame = cv2.resize(sanity_frame, (isz, isz)) if args.image else \
        np.random.randint(0, 255, (isz, isz, 3), dtype=np.uint8)
    tensor = detector._preprocess(bench_frame)  # [1,isz,isz,3] uint8
    interp = detector._interpreter
    in_idx, out_idx = detector._in_index, detector._out_index

    for _ in range(args.warmup):
        interp.set_tensor(in_idx, tensor)
        interp.invoke()
        interp.get_tensor(out_idx)

    tpu = np.empty(args.iters)
    for i in range(args.iters):
        t = time.perf_counter()
        interp.set_tensor(in_idx, tensor)
        interp.invoke()
        interp.get_tensor(out_idx)
        tpu[i] = (time.perf_counter() - t) * 1e3

    full = np.empty(args.iters)
    for i in range(args.iters):
        t = time.perf_counter()
        detector.infer(bench_frame)
        full[i] = (time.perf_counter() - t) * 1e3

    def report(name, a):
        print("  %-18s min=%.2f median=%.2f mean=%.2f p95=%.2f max=%.2f ms  | FPS median=%.1f"
              % (name, a.min(), np.median(a), a.mean(), np.percentile(a, 95), a.max(),
                 1000.0 / np.median(a)))

    print("\n=== latency over %d iters (input=%d, USB3) ===" % (args.iters, isz))
    report("TPU invoke", tpu)
    report("full infer", full)
    print("  decode+dequant overhead ~= %.2f ms (median)"
          % (np.median(full) - np.median(tpu)))


if __name__ == "__main__":
    main()
