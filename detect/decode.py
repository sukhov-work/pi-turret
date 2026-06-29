"""Anchor-free YOLOv8/YOLO11 decode + NMS — pure logic, unit-tested on the Mac.

This is the v1 Coral accuracy fix. The detector head is **anchor-free**: output
``[1, 4+nc, 8400]`` with **no objectness channel** (single class -> ``[1, 5, 8400]``).

Decode = transpose -> ``boxes = out[:, :4]`` (xywh) -> ``scores = out[:, 4:]`` ->
threshold -> NMS. There is **NO objectness multiply** and **NO YOLOv5 anchor grid**.
Applying a YOLOv5 decoder here was the v1 bug; the golden test guards against it.

All returned coordinates are full-frame pixels.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from contracts import Detection


def compute_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    """IoU of one xyxy box against an array of xyxy boxes."""
    xmin = np.maximum(box[0], boxes[:, 0])
    ymin = np.maximum(box[1], boxes[:, 1])
    xmax = np.minimum(box[2], boxes[:, 2])
    ymax = np.minimum(box[3], boxes[:, 3])

    inter = np.maximum(0.0, xmax - xmin) * np.maximum(0.0, ymax - ymin)
    box_area = (box[2] - box[0]) * (box[3] - box[1])
    boxes_area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    union = box_area + boxes_area - inter
    return np.where(union > 0.0, inter / union, 0.0)


def nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    """Greedy single-class NMS. Returns kept indices, highest score first."""
    if boxes_xyxy.shape[0] == 0:
        return []
    order = np.argsort(scores)[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        ious = compute_iou(boxes_xyxy[i, :], boxes_xyxy[order[1:], :])
        remaining = np.where(ious < iou_threshold)[0]
        order = order[remaining + 1]
    return keep


def multiclass_nms(boxes_xyxy: np.ndarray, scores: np.ndarray,
                   class_ids: np.ndarray, iou_threshold: float) -> List[int]:
    """NMS applied independently per class. Returns kept indices."""
    keep: List[int] = []
    for cls in np.unique(class_ids):
        idx = np.where(class_ids == cls)[0]
        cls_keep = nms(boxes_xyxy[idx, :], scores[idx], iou_threshold)
        keep.extend(idx[cls_keep].tolist())
    return keep


def xywh_to_xyxy(xywh: np.ndarray) -> np.ndarray:
    """Center-form (cx, cy, w, h) -> corner-form (x1, y1, x2, y2)."""
    xyxy = np.empty_like(xywh)
    xyxy[..., 0] = xywh[..., 0] - xywh[..., 2] / 2.0
    xyxy[..., 1] = xywh[..., 1] - xywh[..., 3] / 2.0
    xyxy[..., 2] = xywh[..., 0] + xywh[..., 2] / 2.0
    xyxy[..., 3] = xywh[..., 1] + xywh[..., 3] / 2.0
    return xyxy


def _to_anchors_last(raw: np.ndarray) -> np.ndarray:
    """Normalize raw model output to shape ``[num_anchors, 4+nc]``.

    Accepts ``[1, C, N]``, ``[C, N]``, ``[1, N, C]`` or ``[N, C]``. The channel
    dim (``4+nc``, e.g. 5) is always far smaller than the anchor dim (e.g. 8400),
    so we orient by picking the smaller axis as channels.
    """
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim == 3:
        if arr.shape[0] != 1:
            raise ValueError(f"expected batch size 1, got shape {arr.shape}")
        arr = arr[0]
    if arr.ndim != 2:
        raise ValueError(f"cannot decode output of shape {np.asarray(raw).shape}")
    rows, cols = arr.shape
    # Channels is the small dimension; anchors the large one.
    if rows < cols:
        arr = arr.T  # [C, N] -> [N, C]
    return arr


def decode_v8(
    raw_output: np.ndarray,
    input_size_px: int,
    frame_width_px: int,
    frame_height_px: int,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.5,
    num_classes: Optional[int] = None,
    coords_normalized: bool = False,
) -> List[Detection]:
    """Decode an anchor-free YOLOv8/YOLO11 output tensor into ``Detection``s.

    Args:
        raw_output: model output, any of ``[1,4+nc,N] / [4+nc,N] / [1,N,4+nc] / [N,4+nc]``.
        input_size_px: square model input size (e.g. 256).
        frame_width_px/frame_height_px: full-frame size the boxes map back to.
        coords_normalized: True if the model emits xywh in [0,1] (multiply by input
            size first). Default False = xywh already in input pixels (Ultralytics
            default). Pinned by the golden fixture once the real model exists.

    Returns:
        Detections in full-frame pixel coords, post-NMS.
    """
    arr = _to_anchors_last(raw_output)  # [N, 4+nc]
    if arr.shape[1] < 5:
        raise ValueError(f"need >=5 channels (4 box + >=1 class), got {arr.shape[1]}")

    nc = arr.shape[1] - 4 if num_classes is None else int(num_classes)
    boxes_xywh = arr[:, :4].astype(np.float32, copy=True)
    cls_scores = arr[:, 4:4 + nc]

    # Anchor-free: class scores are taken DIRECTLY. No objectness multiply.
    scores = cls_scores.max(axis=1)
    class_ids = cls_scores.argmax(axis=1)

    keep_mask = scores >= conf_threshold
    if not np.any(keep_mask):
        return []
    boxes_xywh = boxes_xywh[keep_mask]
    scores = scores[keep_mask]
    class_ids = class_ids[keep_mask]

    if coords_normalized:
        boxes_xywh *= float(input_size_px)

    boxes_xyxy = xywh_to_xyxy(boxes_xywh)

    # Map model-input pixels -> full-frame pixels.
    sx = float(frame_width_px) / float(input_size_px)
    sy = float(frame_height_px) / float(input_size_px)
    boxes_xyxy[:, [0, 2]] *= sx
    boxes_xyxy[:, [1, 3]] *= sy

    keep = multiclass_nms(boxes_xyxy, scores, class_ids, iou_threshold)
    keep.sort(key=lambda i: scores[i], reverse=True)

    # Clip to frame bounds (matches Ultralytics' clip_boxes): a box can extend past
    # the edge when the target is partly out of view; off-frame corners would skew the
    # centroid the controller aims at. NMS runs on the unclipped boxes, like Ultralytics.
    max_x = float(frame_width_px)
    max_y = float(frame_height_px)
    detections: List[Detection] = []
    for i in keep:
        x1, y1, x2, y2 = boxes_xyxy[i]
        x1 = min(max(float(x1), 0.0), max_x)
        y1 = min(max(float(y1), 0.0), max_y)
        x2 = min(max(float(x2), 0.0), max_x)
        y2 = min(max(float(y2), 0.0), max_y)
        detections.append(
            Detection.from_xyxy(int(class_ids[i]), float(scores[i]), x1, y1, x2, y2)
        )
    return detections
