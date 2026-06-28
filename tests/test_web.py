"""Unit tests for the web controller logic (no Bottle — pure, Mac-runnable).

Covers the seams that matter: telemetry serialization (inf -> None), command
dispatch, manual-jog disarm gating, and live config updates (validation,
atomic rollback, type coercion, and re-applying init-snapshotted values).
"""
from __future__ import annotations

import math

import pytest

from actuate.servo import Axis, ServoController
from app.control import ControlLoop, Telemetry
from app.pipeline import Pipeline
from app.statemachine import FireState, FireStateMachine
from app.web import TurretWebController
from config import Config
from strategy.selector import TargetSelector


@pytest.fixture
def rig(fake_servo_bus, fake_clock):
    cfg = Config()
    servo = ServoController(fake_servo_bus, cfg.servo)
    selector = TargetSelector(cfg.strategy.switch_hysteresis,
                              cfg.strategy.min_target_dwell_frames)
    sm = FireStateMachine(cfg.fire, clock=fake_clock)
    control = ControlLoop(cfg, servo, selector, sm)
    pipeline = Pipeline(capture=None, detector=None, control=control, tracker=None)
    web = TurretWebController(cfg, pipeline, control, jog_step_deg=2.0)
    return cfg, servo, control, pipeline, web


# ---- telemetry ----

def test_telemetry_sanitizes_infinity(rig, track_factory):
    cfg, servo, control, pipeline, web = rig
    pipeline.latest_telemetry.put(Telemetry(
        state=FireState.AIMING, num_tracks=1, selected_target_id=7,
        aim_error_px=float("inf"), predicted_xy=(float("nan"), 12.0),
        pan_cmd_deg=20.0, tilt_cmd_deg=15.0, in_killzone=False, would_fire=False,
    ))
    pipeline.latest_tracks.put([track_factory(track_id=7, cx=100, cy=120)])
    pipeline._fps = 18.5
    pipeline.shots = 3

    out = web.telemetry()
    assert out["state"] == "aiming"
    assert out["aim_error_px"] is None            # inf -> None (JSON-safe)
    assert out["predicted_xy"] == [None, 12.0]    # nan -> None
    assert out["fps"] == 18.5 and out["shots"] == 3
    assert out["selected_target_id"] == 7
    assert out["tracks"][0]["id"] == 7
    # every numeric is JSON-finite
    for v in (out["pan_cmd_deg"], out["tilt_cmd_deg"]):
        assert v is None or math.isfinite(v)


def test_telemetry_without_data_falls_back_to_sm_state(rig):
    cfg, servo, control, pipeline, web = rig
    out = web.telemetry()
    assert out["state"] == FireState.SEARCHING.value
    assert out["tracks"] == []
    assert out["armed"] is True       # SEARCHING is not SAFE


def test_telemetry_includes_tactical_geometry(rig):
    """The web tactical canvas needs frame dims + kill-zone every poll."""
    cfg, servo, control, pipeline, web = rig
    out = web.telemetry()
    assert out["frame"]["w"] == cfg.camera.capture_width_px
    assert out["frame"]["h"] == cfg.camera.capture_height_px
    assert out["killzone"]["cx_px"] == cfg.killzone.cx_px
    assert out["killzone"]["shape"] == cfg.killzone.shape


# ---- commands ----

def test_arm_disarm(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    assert control.sm.state is FireState.SAFE
    assert web.turret_state()["state"] == "Disabled"
    web.command("arm")
    assert control.sm.state is not FireState.SAFE
    assert web.turret_state()["state"] == "Enabled"


def test_fire_toggle(rig):
    cfg, servo, control, pipeline, web = rig
    assert cfg.fire.enabled is False
    assert web.command("enable_fire")["ok"] is True
    assert cfg.fire.enabled is True
    web.command("disable_fire")
    assert cfg.fire.enabled is False
    web.command("toggle_fire")
    assert cfg.fire.enabled is True


def test_center_moves_to_home(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.command("center")["ok"] is True
    assert servo.last_angle(Axis.PAN) == cfg.servo.home_pan_deg
    assert servo.last_angle(Axis.TILT) == cfg.servo.home_tilt_deg


def test_aux_command_applies_to_control(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("enable_aux")
    assert cfg.app.aux_marker_enabled is True
    assert control._aux_enabled is True            # apply_config ran
    web.command("disable_aux")
    assert cfg.app.aux_marker_enabled is False
    assert control._aux_enabled is False


def test_unknown_command(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.command("frobnicate")["ok"] is False


# ---- manual jog ----

def test_manual_jog_refused_when_armed(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("arm")
    res = web.manual_control("up")
    assert res["ok"] is False and "disarmed" in res["error"]


def test_manual_jog_when_disarmed_moves_servo(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    before = servo.last_angle(Axis.PAN)
    res = web.manual_control("left")               # pan + step
    assert res["ok"] is True
    assert servo.last_angle(Axis.PAN) == min(before + 2.0, cfg.servo.pan_max_deg)


def test_manual_stop_is_noop(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    before = servo.last_angle(Axis.TILT)
    assert web.manual_control("stop")["ok"] is True
    assert servo.last_angle(Axis.TILT) == before


# ---- config tuning ----

def test_update_config_valid(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.update_config("strategy", {"w_size": 1.5})
    assert res["ok"] is True
    assert cfg.strategy.w_size == 1.5


def test_update_config_coerces_strings(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.update_config("strategy", {"w_size": "2.0"})  # HTML sends strings
    assert res["ok"] is True
    assert cfg.strategy.w_size == 2.0 and isinstance(cfg.strategy.w_size, float)


def test_update_config_unknown_key_rejected(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.update_config("strategy", {"nope": 1})
    assert res["ok"] is False
    assert not hasattr(cfg.strategy, "nope")


def test_update_config_invalid_value_rolls_back(rig):
    cfg, servo, control, pipeline, web = rig
    before = cfg.fire.fire_duration_s
    res = web.update_config("fire", {"fire_duration_s": -1})  # validate() rejects
    assert res["ok"] is False
    assert cfg.fire.fire_duration_s == before                # section restored


def test_update_config_section_not_tunable(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.update_config("servo", {"pan_max_deg": 90})["ok"] is False


def test_update_config_aim_rebuilds_calibration(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.update_config("aim", {"pan_coeffs": [0.1, 0.2, 3.0]})
    assert res["ok"] is True
    assert control.cal.pan_coeffs == (0.1, 0.2, 3.0)         # apply_config rebuilt cal


def test_update_config_selector_params_live(rig):
    cfg, servo, control, pipeline, web = rig
    web.update_config("strategy", {"switch_hysteresis": 0.5})
    assert control.selector.hysteresis == 0.5


def test_update_config_killzone_atomic_swap(rig):
    cfg, servo, control, pipeline, web = rig
    old = cfg.killzone
    res = web.update_config("killzone", {"cx_px": 400.0})
    assert res["ok"] is True
    assert cfg.killzone is not old                           # whole-section swap
    assert cfg.killzone.cx_px == 400.0


# ---- USB stream switching (Step 1.12) ----

class _FakeStreamer:
    def __init__(self):
        self.running = False
        self.starts = 0

    def start(self):
        self.starts += 1
        self.running = True
        return True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running


def _web_with_streamer(rig, streamer):
    cfg, servo, control, pipeline, _ = rig
    return TurretWebController(cfg, pipeline, control, streamer=streamer)


def test_telemetry_stream_block_without_streamer(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.telemetry()["stream"]["running"] is False
    assert web.telemetry()["stream"]["source"] == cfg.app.stream_source


def test_stream_usb_starts_and_sets_source(rig):
    streamer = _FakeStreamer()
    web = _web_with_streamer(rig, streamer)
    res = web.command("stream_usb")
    assert res["ok"] is True and res["stream_running"] is True
    assert streamer.running is True
    assert web.cfg.app.stream_source == "usb"
    assert web.telemetry()["stream"]["running"] is True


def test_stream_off_stops(rig):
    streamer = _FakeStreamer()
    web = _web_with_streamer(rig, streamer)
    web.command("stream_usb")
    res = web.command("stream_off")
    assert res["ok"] is True and res["stream_running"] is False
    assert streamer.running is False


def test_stream_command_without_streamer_errors(rig):
    cfg, servo, control, pipeline, web = rig   # rig's web has no streamer
    assert web.command("stream_usb")["ok"] is False
