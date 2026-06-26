"""Detector interface — the single seam the compile-gate decision hides behind.

``strategy``/``aim`` never know whether a Coral YOLOv8, Coral MobileDet, or CPU
backend ran: they only see ``list[Detection]`` in full-frame pixels.
"""
from __future__ import annotations

import abc
from typing import List

import numpy as np

from contracts import Detection


class Detector(abc.ABC):
    """Abstract detector. Backends implement ``infer`` and return full-frame dets."""

    @abc.abstractmethod
    def infer(self, frame: np.ndarray) -> List[Detection]:
        """Run inference on a single frame and return detections."""

    def close(self) -> None:  # optional override for backends holding resources
        pass
