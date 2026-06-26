"""Target selection with hysteresis (pure logic).

Picks the highest-scoring track, but only **switches** away from the current
target when a rival beats it by more than ``switch_hysteresis`` AND the current
target has been held at least ``min_target_dwell_frames``. This stops the turret
oscillating between two similar birds. If the current target disappears, switch
to the best remaining one immediately.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

ScoredTrack = Tuple[int, float]  # (track_id, score)


class TargetSelector:
    def __init__(self, switch_hysteresis: float = 0.15,
                 min_target_dwell_frames: int = 5):
        self.hysteresis = switch_hysteresis
        self.min_dwell = min_target_dwell_frames
        self._current_id: Optional[int] = None
        self._frames_on_target = 0

    @property
    def current_id(self) -> Optional[int]:
        return self._current_id

    def reset(self) -> None:
        self._current_id = None
        self._frames_on_target = 0

    def select(self, scored: Sequence[ScoredTrack]) -> Optional[int]:
        if not scored:
            self.reset()
            return None

        scores: Dict[int, float] = {tid: s for tid, s in scored}
        best_id = max(scores, key=lambda tid: scores[tid])

        # No current target, or it vanished -> take the best now.
        if self._current_id is None or self._current_id not in scores:
            return self._commit(best_id)

        # Best is still the current target -> hold.
        if best_id == self._current_id:
            self._frames_on_target += 1
            return self._current_id

        # A rival leads. Switch only past the hysteresis margin and min dwell.
        rival_lead = scores[best_id] - scores[self._current_id]
        if rival_lead > self.hysteresis and self._frames_on_target >= self.min_dwell:
            return self._commit(best_id)

        self._frames_on_target += 1
        return self._current_id

    def _commit(self, target_id: int) -> int:
        if target_id != self._current_id:
            self._current_id = target_id
            self._frames_on_target = 1
        else:
            self._frames_on_target += 1
        return target_id
