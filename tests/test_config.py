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


def test_loads_repo_config_yaml():
    path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    cfg = load_config(os.path.abspath(path))
    assert cfg.servo.pan_max_deg == 47.0
    assert cfg.detector.input_size_px == 256
