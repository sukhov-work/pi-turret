"""Config: defaults, round-trip, validation, yaml load."""
import os

import pytest

from config import Config, load_config
from errors import ConfigError


def test_defaults_validate_and_carry_v1_hardware_map():
    cfg = Config.from_dict({})
    assert cfg.servo.pan_max_deg == 47.0
    assert cfg.servo.tilt_max_deg == 25.0
    assert cfg.servo.pan_channel == 1 and cfg.servo.tilt_channel == 0
    assert cfg.pump.pump_gpio_bcm == 26
    assert cfg.fire.enabled is False  # SAFE default


def test_round_trip():
    cfg = Config.from_dict({})
    again = Config.from_dict(cfg.to_dict())
    assert again.to_dict() == cfg.to_dict()


def test_override_merges_over_defaults():
    cfg = Config.from_dict({"detector": {"conf_threshold": 0.4}})
    assert cfg.detector.conf_threshold == 0.4
    assert cfg.detector.iou_threshold == 0.5  # untouched default


def test_unknown_key_rejected():
    with pytest.raises(ConfigError):
        Config.from_dict({"servo": {"nope": 1}})


def test_bad_clamp_rejected():
    with pytest.raises(ConfigError):
        Config.from_dict({"servo": {"pan_min_deg": 50, "pan_max_deg": 10}})


def test_threshold_out_of_range_rejected():
    with pytest.raises(ConfigError):
        Config.from_dict({"detector": {"conf_threshold": 1.5}})


def test_bad_camera_rotation_rejected():
    with pytest.raises(ConfigError):
        Config.from_dict({"camera": {"rotation_deg": 45}})


def test_camera_rotation_quarter_turns_ok():
    for deg in (0, 90, 180, 270):
        assert Config.from_dict({"camera": {"rotation_deg": deg}}).camera.rotation_deg == deg


def test_loads_repo_config_yaml():
    path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    cfg = load_config(os.path.abspath(path))
    assert cfg.servo.pan_max_deg == 47.0
    assert cfg.detector.input_size_px == 256
    # the deployed stream binary points at v1's committed ARM build (not bare PATH)
    assert cfg.stream.binary.endswith("_build/mjpg_streamer")
    assert cfg.camera.rotation_deg in (0, 90, 180, 270)


def test_save_local_overlay_then_load_roundtrips(tmp_path):
    yaml = pytest.importorskip("yaml")
    from config import save_local_config

    base = tmp_path / "config.yaml"
    local = tmp_path / "config.local.yaml"
    base.write_text("servo:\n  home_pan_deg: 26.0\n")
    cfg = load_config(str(base), str(local))
    cfg.servo.home_pan_deg = 33.0
    cfg.aim.pan_coeffs = [0.1, 0.2, 3.0]
    delta = save_local_config(cfg, str(local), str(base))
    assert delta["servo"]["home_pan_deg"] == 33.0 and "aim" in delta
    reloaded = load_config(str(base), str(local))
    assert reloaded.servo.home_pan_deg == 33.0          # calibrated home restored
    assert reloaded.aim.pan_coeffs == [0.1, 0.2, 3.0]


def test_local_overlay_only_shadows_touched_keys(tmp_path):
    pytest.importorskip("yaml")
    from config import save_local_config

    base = tmp_path / "config.yaml"
    local = tmp_path / "config.local.yaml"
    base.write_text("strategy:\n  w_size: 0.5\n  w_dwell: 0.3\n")
    cfg = load_config(str(base), str(local))
    cfg.strategy.w_size = 1.25
    save_local_config(cfg, str(local), str(base))
    base.write_text("strategy:\n  w_size: 0.5\n  w_dwell: 0.9\n")   # edit an untouched key
    reloaded = load_config(str(base), str(local))
    assert reloaded.strategy.w_size == 1.25             # overlay wins for touched key
    assert reloaded.strategy.w_dwell == 0.9             # base edit applies (not in overlay)
