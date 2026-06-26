"""Strategy layer: per-track scoring + target selection/switching."""
from strategy.scoring import score_track
from strategy.selector import TargetSelector

__all__ = ["score_track", "TargetSelector"]
