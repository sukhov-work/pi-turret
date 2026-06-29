"""rotate_frame: pure orientation correction (Mac-runnable; no picamera2)."""
import numpy as np

from capture import rotate_frame


def test_rotate_0_is_identity_no_copy():
    a = np.arange(9, dtype=np.uint8).reshape(3, 3)
    assert rotate_frame(a, 0) is a


def test_rotate_quarter_turns_match_numpy_and_are_contiguous():
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    for deg in (90, 180, 270):
        out = rotate_frame(a, deg)
        assert np.array_equal(out, np.rot90(a, deg // 90))
        assert out.flags["C_CONTIGUOUS"]          # Edge-TPU needs contiguous input


def test_rotate_square_frame_keeps_dims():
    a = np.zeros((256, 256), dtype=np.uint8)
    for deg in (0, 90, 180, 270):
        assert rotate_frame(a, deg).shape == (256, 256)


def test_picam_apply_config_repoints_cfg():
    from config import Config
    from capture import PiCamCapture
    cfg = Config()
    cap = PiCamCapture(cfg.camera, cfg.detector.input_size_px)
    new = Config()
    new.camera.rotation_deg = 90
    cap.apply_config(new.camera)
    assert cap._cfg.rotation_deg == 90      # live: read per-frame in read_frame
