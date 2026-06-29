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


def test_marker_commands_force_control_marker(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.command("marker_on")
    assert res["ok"] is True and res["marker_on"] is True
    assert control._marker_on is True
    res = web.command("marker_off")
    assert res["ok"] is True and res["marker_on"] is False
    assert control._marker_on is False


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


def test_jog_up_raises_aim_down_lowers(rig):
    """UP must raise the aim; on this rig that means a LOWER tilt angle."""
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    t0 = servo.last_angle(Axis.TILT)
    web.manual_control("up")
    assert servo.last_angle(Axis.TILT) == max(t0 - 2.0, cfg.servo.tilt_min_deg)
    t1 = servo.last_angle(Axis.TILT)
    web.manual_control("down")
    assert servo.last_angle(Axis.TILT) == min(t1 + 2.0, cfg.servo.tilt_max_deg)


def test_manual_fire_command_triggers_pump_then_stop(rig):
    cfg, servo, control, pipeline, web = rig
    fires, offs = [], []
    class _P:
        def fire(self, d): fires.append(d)
        def off(self): offs.append(1)
    control._pump = _P()
    web.command("disarm")                       # works even when disarmed
    assert web.command("fire_now")["ok"] is True
    assert fires == [cfg.fire.fire_duration_s]
    assert web.command("pump_off")["ok"] is True
    assert offs == [1]


def test_manual_fire_without_pump_reports_error(rig):
    cfg, servo, control, pipeline, web = rig   # rig control has no pump
    assert web.command("fire_now")["ok"] is False


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


def test_update_config_unknown_section_rejected(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.update_config("nonsense", {"x": 1})["ok"] is False


def test_all_sections_are_editable_and_snapshotted(rig):
    cfg, servo, control, pipeline, web = rig
    snap = web.config_snapshot()
    for s in ("camera", "stream", "detector", "tracker", "predict", "strategy",
              "killzone", "aim", "controller", "servo", "pump", "fire", "app", "remote"):
        assert s in snap, s


def test_update_servo_section_applies_to_live_servo(rig):
    cfg, servo, control, pipeline, web = rig
    res = web.update_config("servo", {"pan_max_deg": 40.0})   # hardware section now tunable
    assert res["ok"] is True
    assert cfg.servo.pan_max_deg == 40.0
    assert servo._cfg.pan_max_deg == 40.0                     # _resync re-pointed the live servo


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


# ---- detection-cam debug video (Phase B) ----

def test_detection_video_disabled_returns_none(rig):
    cfg, servo, control, pipeline, web = rig
    assert cfg.app.detection_video_enabled is False
    assert web.detection_jpeg() is None            # gated off: never touches cv2


def test_detection_video_command_toggles_flag(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.command("detect_video_on")["ok"] is True
    assert cfg.app.detection_video_enabled is True
    assert web.command("detect_video_off")["ok"] is True
    assert cfg.app.detection_video_enabled is False


def test_detection_video_enabled_but_no_frame_returns_none(rig):
    cfg, servo, control, pipeline, web = rig
    cfg.app.detection_video_enabled = True
    assert pipeline.latest_frame.get() is None
    assert web.detection_jpeg() is None            # no frame yet -> None, no cv2


def test_detection_jpeg_encodes_once_then_serves_cache(rig, monkeypatch):
    import numpy as np

    import app.debugview as dv
    cfg, servo, control, pipeline, web = rig
    cfg.app.detection_video_enabled = True
    cfg.app.detection_video_max_fps = 5.0
    pipeline.latest_frame.put(np.zeros((256, 256), np.uint8))
    calls = {"n": 0}
    monkeypatch.setattr(dv, "encode_jpeg",
                        lambda frame, q: (calls.__setitem__("n", calls["n"] + 1) or b"JPG"))
    first = web.detection_jpeg()
    second = web.detection_jpeg()                  # within 1/5s -> cached, no re-encode
    assert first == second == b"JPG"
    assert calls["n"] == 1


def test_telemetry_exposes_detection_video_flag(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.telemetry()["detection_video"]["enabled"] is False


# ---- calibration + persistence (Phase C) ----

def test_set_home_records_current_pose(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    web.manual_control("left")                      # nudge pan while disarmed
    pan, tilt = servo.last_angle(Axis.PAN), servo.last_angle(Axis.TILT)
    res = web.calibrate("set_home")
    assert res["ok"] is True
    assert cfg.servo.home_pan_deg == round(pan, 2)
    assert cfg.servo.home_tilt_deg == round(tilt, 2)


def test_set_rotation_live_and_validated(rig):
    cfg, servo, control, pipeline, web = rig
    assert web.calibrate("set_rotation", {"rotation_deg": 90})["ok"] is True
    assert cfg.camera.rotation_deg == 90           # in-place; capture reads it per-frame
    bad = web.calibrate("set_rotation", {"rotation_deg": 45})
    assert bad["ok"] is False
    assert cfg.camera.rotation_deg == 90           # rolled back to last good


def test_set_limits_refused_when_armed(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("arm")
    assert web.calibrate("set_limits", {"pan_max_deg": 40})["ok"] is False


def test_set_limits_applies_in_place_and_rolls_back(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    assert web.calibrate("set_limits", {"pan_max_deg": 40.0})["ok"] is True
    assert cfg.servo.pan_max_deg == 40.0            # live ServoController shares this object
    assert servo._cfg.pan_max_deg == 40.0
    before = cfg.servo.pan_min_deg
    bad = web.calibrate("set_limits", {"pan_min_deg": 99.0})  # min >= max -> validate fails
    assert bad["ok"] is False
    assert cfg.servo.pan_min_deg == before          # rolled back


def test_calibration_add_fit_applies_live(rig):
    cfg, servo, control, pipeline, web = rig
    web.command("disarm")
    for i, (cx, cy) in enumerate([(100, 100), (900, 200), (500, 900)]):
        servo.set_angle(Axis.PAN, 10 + i * 5)
        servo.set_angle(Axis.TILT, 8 + i * 3)
        web.calibrate("add_sample", {"cx": cx, "cy": cy})
    assert web.telemetry()["cal_samples"] == 3
    res = web.calibrate("fit")
    assert res["ok"] is True
    assert control.cal.pan_coeffs == tuple(cfg.aim.pan_coeffs)   # fit applied to live aim


def test_calibration_fit_needs_three_samples(rig):
    cfg, servo, control, pipeline, web = rig
    web.calibrate("clear")
    web.calibrate("add_sample", {"cx": 1, "cy": 2})
    assert web.calibrate("fit")["ok"] is False


def test_save_config_writes_overlay(rig, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    cfg, servo, control, pipeline, web = rig
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("servo:\n  home_pan_deg: 26.0\n")
    cfg.servo.home_pan_deg = 30.0
    res = web.save_config()
    assert res["ok"] is True and "servo" in res["saved_sections"]
    assert (tmp_path / "config.local.yaml").exists()
