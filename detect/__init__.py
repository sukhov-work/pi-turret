"""Detection layer: backend-agnostic detector interface + anchor-free v8 decode."""
from detect.base import Detector
from detect.coral import CoralDetector
from detect.decode import (
    compute_iou,
    decode_v8,
    multiclass_nms,
    nms,
    xywh_to_xyxy,
)

__all__ = [
    "Detector",
    "CoralDetector",
    "decode_v8",
    "nms",
    "multiclass_nms",
    "compute_iou",
    "xywh_to_xyxy",
]
