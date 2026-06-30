"""Detection snapshot capture for the future training flywheel.

Saves a crop + full frame + rich metadata so detections can be auto-labelled
later (Phase 4 multi-class). ``build_metadata`` is pure and unit-tested; the
file/image writes (lazy cv2) are Pi-side. Modes: off | every | fire_only | sampled.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from contracts import Track


def build_metadata(track: Track, timestamp: Optional[float] = None,
                   predicted_xy: Optional[tuple] = None,
                   fired: bool = False) -> Dict[str, Any]:
    """Assemble the per-snapshot metadata record (pure)."""
    return {
        "timestamp": timestamp if timestamp is not None else time.time(),
        "track_id": track.id,
        "cls_id": track.cls_id,
        "score": track.score,
        "xyxy": list(track.xyxy),
        "cx": track.cx,
        "cy": track.cy,
        "vx": track.vx,
        "vy": track.vy,
        "hits": track.hits,
        "predicted_xy": list(predicted_xy) if predicted_xy is not None else None,
        "fired": fired,
    }


def save_snapshot(out_dir: str, frame, track: Track, meta: Dict[str, Any]) -> str:
    """Write the full frame, the crop, and the metadata JSON. Returns the base path."""
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{int(meta['timestamp'] * 1000)}_{track.id}")
    cv2.imwrite(base + "_full.jpg", frame)
    x1, y1, x2, y2 = (int(v) for v in track.xyxy)
    crop = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size:
        cv2.imwrite(base + "_crop.jpg", crop)
    with open(base + ".json", "w") as fh:
        json.dump(meta, fh)
    return base


def snapshot_filename(timestamp: Optional[float] = None) -> str:
    """Readable, chronologically-sortable JPEG name for a manual snapshot (pure)."""
    ts = time.time() if timestamp is None else timestamp
    return "snap_%s_%03d.jpg" % (
        time.strftime("%Y%m%d_%H%M%S", time.localtime(ts)), int(ts * 1000) % 1000)


def save_frame(out_dir: str, frame, timestamp: Optional[float] = None,
               quality: int = 95) -> str:
    """Write the current detection frame as one timestamped JPEG; return its path.

    The manual "Save Snapshot" path: unlike ``save_snapshot`` it needs no ``Track``
    (it captures whatever the detector currently sees). Lazy cv2 (Pi-side);
    ``snapshot_filename`` is the pure, Mac-tested part. Raises on a failed write.
    """
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, snapshot_filename(timestamp))
    if not cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]):
        raise OSError("cv2.imwrite failed for " + path)
    return path
