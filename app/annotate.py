"""Opt-in annotation (debug/stream). Runs on a copy; never blocks the control loop.

Headless is the default: in live mode no Pi-Camera frames are drawn. When enabled,
modes are ``off`` | ``fire_frames_only`` | ``full_video``. Drawing uses lazy cv2
(Pi-side); ``should_annotate`` is pure and decides per-frame whether to draw.
"""
from __future__ import annotations

from typing import Sequence

from app.statemachine import FireState
from contracts import Track


def should_annotate(mode: str, state: FireState) -> bool:
    if mode == "full_video":
        return True
    if mode == "fire_frames_only":
        return state is FireState.FIRING
    return False


def draw_overlay(frame, tracks: Sequence[Track], state: FireState,
                 selected_id=None):
    """Return an annotated copy of ``frame`` (boxes, ids, state). Lazy cv2."""
    import cv2
    img = frame.copy()
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for t in tracks:
        x1, y1, x2, y2 = (int(v) for v in t.xyxy)
        color = (0, 0, 255) if t.id == selected_id else (0, 200, 0)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(img, f"#{t.id} {t.score:.2f}", (x1, max(0, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    cv2.putText(img, state.value, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2, cv2.LINE_AA)
    return img
