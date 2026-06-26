"""Golden tests for the anchor-free v8 decode — the v5/v8 guardrail.

These lock the decode contract independent of any real model:
  - transpose [1, 4+nc, N] -> [N, 4+nc]
  - NO objectness multiply (scores come straight from the class channels)
  - xywh -> xyxy, mapped from model-input px to full-frame px
  - correct class index via argmax
If anyone reintroduces a YOLOv5 (objectness) decoder, these go red.
"""
import json
import os

import numpy as np
import pytest

from contracts import Detection
from detect.decode import decode_v8, nms, xywh_to_xyxy


INPUT = 256
FRAME = 1152  # full-frame square; scale factor 4.5x


def _blank_output(n_anchors=8400, nc=1):
    """A [1, 4+nc, N] tensor with everything below threshold."""
    return np.zeros((1, 4 + nc, n_anchors), dtype=np.float32)


def _plant(out, anchor, cx, cy, w, h, cls, score):
    """Plant one box (model-input pixel coords) at an anchor column."""
    out[0, 0, anchor] = cx
    out[0, 1, anchor] = cy
    out[0, 2, anchor] = w
    out[0, 3, anchor] = h
    out[0, 4 + cls, anchor] = score


def test_decode_single_box_maps_to_full_frame():
    out = _blank_output()
    # center of the model input -> center of the full frame
    _plant(out, 0, cx=128, cy=128, w=20, h=10, cls=0, score=0.9)
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25)
    assert len(dets) == 1
    d = dets[0]
    assert d.cls_id == 0
    assert d.score == pytest.approx(0.9)
    # 128px at input 256 -> 576px at frame 1152 (scale 4.5)
    assert d.cx == pytest.approx(576.0)
    assert d.cy == pytest.approx(576.0)
    # width 20 input px -> 90 frame px
    x1, y1, x2, y2 = d.xyxy
    assert (x2 - x1) == pytest.approx(90.0)
    assert (y2 - y1) == pytest.approx(45.0)


def test_decode_no_objectness_multiply():
    """Score must equal the raw class score, NOT class*objectness.

    A YOLOv5 decoder would treat channel 4 as objectness and multiply, halving
    this score. Anchor-free v8 has no such channel.
    """
    out = _blank_output(nc=1)
    _plant(out, 5, cx=100, cy=100, w=10, h=10, cls=0, score=0.8)
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25)
    assert len(dets) == 1
    assert dets[0].score == pytest.approx(0.8)  # not 0.8*0.8=0.64


def test_decode_multiclass_argmax_picks_right_class():
    out = _blank_output(nc=3)
    # class 2 is the strongest -> argmax must select it
    out[0, 4 + 0, 0] = 0.30
    out[0, 4 + 1, 0] = 0.40
    out[0, 4 + 2, 0] = 0.85
    out[0, 0, 0] = 100
    out[0, 1, 0] = 100
    out[0, 2, 0] = 10
    out[0, 3, 0] = 10
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25)
    assert len(dets) == 1
    assert dets[0].cls_id == 2
    assert dets[0].score == pytest.approx(0.85)


def test_decode_threshold_filters_low_scores():
    out = _blank_output()
    _plant(out, 0, 100, 100, 10, 10, cls=0, score=0.20)
    assert decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25) == []


def test_decode_accepts_already_transposed_layout():
    # [1, N, 4+nc] layout should decode identically.
    out = _blank_output()
    _plant(out, 0, 128, 128, 20, 20, cls=0, score=0.9)
    transposed = np.transpose(out, (0, 2, 1))  # [1, N, 4+nc]
    dets = decode_v8(transposed, INPUT, FRAME, FRAME, conf_threshold=0.25)
    assert len(dets) == 1
    assert dets[0].cx == pytest.approx(576.0)


def test_decode_normalized_coords_scale_by_input_size():
    out = _blank_output()
    # normalized: center (0.5, 0.5) of a 256 input -> 128px -> 576 frame px
    _plant(out, 0, cx=0.5, cy=0.5, w=0.1, h=0.1, cls=0, score=0.9)
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25,
                     coords_normalized=True)
    assert len(dets) == 1
    assert dets[0].cx == pytest.approx(576.0)


def test_decode_nms_suppresses_duplicate_boxes():
    out = _blank_output()
    # two near-identical boxes; NMS keeps the higher score
    _plant(out, 0, 100, 100, 30, 30, cls=0, score=0.9)
    _plant(out, 1, 101, 101, 30, 30, cls=0, score=0.6)
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25, iou_threshold=0.5)
    assert len(dets) == 1
    assert dets[0].score == pytest.approx(0.9)


def test_decode_empty_when_all_below_threshold():
    assert decode_v8(_blank_output(), INPUT, FRAME, FRAME) == []


def test_decode_sorted_by_score_desc():
    out = _blank_output()
    _plant(out, 0, 40, 40, 10, 10, cls=0, score=0.5)
    _plant(out, 1, 200, 200, 10, 10, cls=0, score=0.95)
    dets = decode_v8(out, INPUT, FRAME, FRAME, conf_threshold=0.25)
    assert [round(d.score, 2) for d in dets] == [0.95, 0.5]


def test_xywh_to_xyxy_roundtrip():
    xywh = np.array([[50.0, 60.0, 20.0, 10.0]])
    xyxy = xywh_to_xyxy(xywh)
    assert list(xyxy[0]) == [40.0, 55.0, 60.0, 65.0]


# ---- Real-model golden test (skipped until the fixture is captured on Strix/Pi) ----

_FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_RAW = os.path.join(_FIX_DIR, "raw_output.npy")
_REF = os.path.join(_FIX_DIR, "predict_ref.json")


@pytest.mark.skipif(
    not (os.path.exists(_RAW) and os.path.exists(_REF)),
    reason="real-model fixtures not yet captured (needs model.predict on Strix/Pi)",
)
def test_v8_decode_matches_ultralytics_reference():
    raw = np.load(_RAW)
    ref = json.load(open(_REF))
    dets = decode_v8(
        raw,
        input_size_px=ref["input_size_px"],
        frame_width_px=ref["frame_width_px"],
        frame_height_px=ref["frame_height_px"],
        conf_threshold=ref.get("conf", 0.25),
        iou_threshold=ref.get("iou", 0.5),
        coords_normalized=ref.get("coords_normalized", False),
    )
    expected = ref["boxes"]
    assert len(dets) == len(expected)
    for d, e in zip(sorted(dets, key=lambda x: -x.score), expected):
        assert d.cls_id == e["cls_id"]
        for got, want in zip(d.xyxy, e["xyxy"]):
            assert got == pytest.approx(want, abs=2.0)
