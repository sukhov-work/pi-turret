"""Lightweight multi-object tracker: greedy IoU association + constant-velocity.

Chosen over ByteTrack for P1: zero extra deps, pure Python/NumPy, Python-3.9 clean,
and fully unit-testable on the Mac. It works with **any** ``Detection`` source, so
swapping in ByteTrack later (if the chosen detector integrates it cleanly) is a
``track/`` change that leaves strategy/aim untouched. Velocity (px/frame) is an
EMA-smoothed finite difference, feeding the lead predictor.
"""
from __future__ import annotations

from typing import List, Sequence

from contracts import Detection, Track


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


class IouTracker:
    """Greedy IoU tracker producing stable integer track ids.

    A track is *returned* (active) once it reaches ``min_hits`` and while it has
    been seen within ``max_age_frames`` — so a briefly-occluded target keeps its
    id and the predictor can extrapolate across the gap.
    """

    def __init__(self, iou_match_threshold: float = 0.3, max_age_frames: int = 30,
                 min_hits: int = 3, velocity_smoothing: float = 0.5):
        self.iou_threshold = iou_match_threshold
        self.max_age_frames = max_age_frames
        self.min_hits = min_hits
        self.alpha = velocity_smoothing
        self._tracks: List[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self._tracks = []
        self._next_id = 1

    def update(self, detections: Sequence[Detection]) -> List[Track]:
        detections = list(detections)
        matches = self._match(detections)

        matched_tracks = set()
        matched_dets = set()
        for ti, di in matches:
            self._update_track(self._tracks[ti], detections[di])
            matched_tracks.add(ti)
            matched_dets.add(di)

        # Age unmatched tracks.
        for ti, track in enumerate(self._tracks):
            if ti not in matched_tracks:
                track.time_since_update += 1
                track.age += 1

        # Spawn tracks for unmatched detections.
        for di, det in enumerate(detections):
            if di not in matched_dets:
                self._tracks.append(self._new_track(det))

        # Drop stale tracks.
        self._tracks = [t for t in self._tracks
                        if t.time_since_update <= self.max_age_frames]

        return [t for t in self._tracks if t.hits >= self.min_hits]

    @property
    def tracks(self) -> List[Track]:
        """All live tracks (including tentative ones below min_hits)."""
        return list(self._tracks)

    def _match(self, detections: Sequence[Detection]):
        candidates = []
        for ti, track in enumerate(self._tracks):
            for di, det in enumerate(detections):
                iou = _iou(track.xyxy, det.xyxy)
                if iou >= self.iou_threshold:
                    candidates.append((iou, ti, di))
        candidates.sort(reverse=True)  # highest IoU first
        used_t, used_d = set(), set()
        matches = []
        for _, ti, di in candidates:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))
        return matches

    def _new_track(self, det: Detection) -> Track:
        track = Track(
            id=self._next_id, cls_id=det.cls_id, score=det.score, xyxy=det.xyxy,
            cx=det.cx, cy=det.cy, vx=0.0, vy=0.0, age=1, hits=1, time_since_update=0,
        )
        self._next_id += 1
        return track

    def _update_track(self, track: Track, det: Detection) -> None:
        steps = track.time_since_update + 1  # frames since last position update
        inst_vx = (det.cx - track.cx) / steps
        inst_vy = (det.cy - track.cy) / steps
        track.vx = self.alpha * inst_vx + (1.0 - self.alpha) * track.vx
        track.vy = self.alpha * inst_vy + (1.0 - self.alpha) * track.vy
        track.cx, track.cy = det.cx, det.cy
        track.xyxy = det.xyxy
        track.score = det.score
        track.cls_id = det.cls_id
        track.hits += 1
        track.age += 1
        track.time_since_update = 0
