"""NMS / IoU unit tests."""
import numpy as np
import pytest

from detect.decode import compute_iou, multiclass_nms, nms


def test_iou_identical_boxes_is_one():
    box = np.array([0.0, 0.0, 10.0, 10.0])
    boxes = np.array([[0.0, 0.0, 10.0, 10.0]])
    assert compute_iou(box, boxes)[0] == pytest.approx(1.0)


def test_iou_disjoint_boxes_is_zero():
    box = np.array([0.0, 0.0, 10.0, 10.0])
    boxes = np.array([[20.0, 20.0, 30.0, 30.0]])
    assert compute_iou(box, boxes)[0] == pytest.approx(0.0)


def test_iou_half_overlap():
    box = np.array([0.0, 0.0, 10.0, 10.0])
    boxes = np.array([[5.0, 0.0, 15.0, 10.0]])  # 50px inter, 150px union
    assert compute_iou(box, boxes)[0] == pytest.approx(50.0 / 150.0)


def test_nms_keeps_highest_and_suppresses_overlap():
    boxes = np.array([
        [0.0, 0.0, 10.0, 10.0],
        [1.0, 1.0, 11.0, 11.0],   # overlaps box 0 strongly
        [50.0, 50.0, 60.0, 60.0],
    ])
    scores = np.array([0.9, 0.8, 0.7])
    keep = nms(boxes, scores, iou_threshold=0.5)
    assert set(keep) == {0, 2}
    assert keep[0] == 0  # highest score first


def test_nms_empty_input():
    assert nms(np.empty((0, 4)), np.empty((0,)), 0.5) == []


def test_nms_single_box():
    assert nms(np.array([[0.0, 0.0, 5.0, 5.0]]), np.array([0.5]), 0.5) == [0]


def test_multiclass_nms_does_not_suppress_across_classes():
    # two heavily-overlapping boxes of different classes both survive
    boxes = np.array([
        [0.0, 0.0, 10.0, 10.0],
        [0.0, 0.0, 10.0, 10.0],
    ])
    scores = np.array([0.9, 0.8])
    class_ids = np.array([0, 1])
    keep = multiclass_nms(boxes, scores, class_ids, iou_threshold=0.5)
    assert set(keep) == {0, 1}
