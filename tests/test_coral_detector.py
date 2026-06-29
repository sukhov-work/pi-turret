"""CoralDetector coordinate-space wiring (Mac: construct only, no interpreter).

The detector runs on the small lores frame but its detections must land in the
full capture-frame pixel space that the killzone, scoring, calibration and the
tactical canvas share. Guards the 256-vs-1152 mismatch bug.
"""
import numpy as np

from config import Config
from detect.coral import CoralDetector


def test_frame_dims_use_configured_full_frame_not_lores():
    cfg = Config()
    det = CoralDetector(cfg.detector,
                        frame_width_px=cfg.camera.capture_width_px,
                        frame_height_px=cfg.camera.capture_height_px)
    lores = np.zeros((cfg.detector.input_size_px, cfg.detector.input_size_px), np.uint8)
    assert det._frame_dims(lores) == (1152, 1152)   # map 256px detections -> 1152px frame


def test_frame_dims_fall_back_to_frame_shape_when_unset():
    cfg = Config()
    det = CoralDetector(cfg.detector)
    frame = np.zeros((256, 256), np.uint8)
    assert det._frame_dims(frame) == (256, 256)
