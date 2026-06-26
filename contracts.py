"""Frozen data contracts shared across layers: ``Detection`` and ``Track``.

These are the stable interface between detect -> track -> strategy -> aim. All
coordinates are **full-frame pixels** (the detector maps back from model input),
so nothing downstream needs to know the model input size or which backend ran.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class Detection:
    """One detection in full-frame pixel coordinates.

    xyxy is (x1, y1, x2, y2); cx/cy is the box centroid.
    """

    cls_id: int
    score: float
    xyxy: Tuple[float, float, float, float]
    cx: float
    cy: float

    @classmethod
    def from_xyxy(cls, cls_id: int, score: float, x1: float, y1: float,
                  x2: float, y2: float) -> "Detection":
        return cls(
            cls_id=int(cls_id),
            score=float(score),
            xyxy=(float(x1), float(y1), float(x2), float(y2)),
            cx=(float(x1) + float(x2)) / 2.0,
            cy=(float(y1) + float(y2)) / 2.0,
        )

    @property
    def area_px(self) -> float:
        x1, y1, x2, y2 = self.xyxy
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass
class Track:
    """A detection associated across frames with a stable id and velocity.

    vx/vy are in pixels-per-frame. ``time_since_update`` counts frames since the
    track was last matched to a detection (0 = updated this frame).
    """

    id: int
    cls_id: int
    score: float
    xyxy: Tuple[float, float, float, float]
    cx: float
    cy: float
    vx: float = 0.0
    vy: float = 0.0
    age: int = 0
    hits: int = 0
    time_since_update: int = 0

    @property
    def area_px(self) -> float:
        x1, y1, x2, y2 = self.xyxy
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)
