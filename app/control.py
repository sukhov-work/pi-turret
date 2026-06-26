"""Control composition: one tick from tracks -> aim -> fire decision.

This is the single owner of servo motion and the fire state machine. It is kept
out of the thread machinery (``pipeline.py``) so it can be integration-tested on
the Mac with a fake servo driver, asserting that clamps and the fire predicate
are honored. The default aim path is calibration feed-forward (the fitted
pixel->angle transform) with a per-tick slew cap; ``PIController`` is available as
an alternative pixel-centering trim mode tuned on the Pi.

Real settling/aiming behaviour is Pi-only truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from aim.calibrate import Calibration, apply_aim_offsets, apply_calibration
from aim.controller import slew_toward
from aim.killzone import distance_to_center_px, is_in_kill_zone
from actuate.servo import Axis, ServoController
from app.statemachine import FireContext, FireState, FireStateMachine
from config import Config
from contracts import Track
from strategy.scoring import score_track
from strategy.selector import TargetSelector
from track.predict import predict_lead


@dataclass
class Telemetry:
    state: FireState
    num_tracks: int
    selected_target_id: Optional[int]
    aim_error_px: float
    predicted_xy: Optional[tuple]
    pan_cmd_deg: Optional[float]
    tilt_cmd_deg: Optional[float]
    in_killzone: bool
    would_fire: bool


class ControlLoop:
    def __init__(self, cfg: Config, servo: ServoController,
                 selector: TargetSelector, state_machine: FireStateMachine,
                 calibration: Optional[Calibration] = None,
                 status_led=None, aux_marker=None):
        self.cfg = cfg
        self.servo = servo
        self.selector = selector
        self.sm = state_machine
        self.cal = calibration or Calibration.from_config(cfg.aim)
        self._frame_w = float(cfg.camera.capture_width_px)
        self._frame_h = float(cfg.camera.capture_height_px)
        # Optional indicators (BCM23 status LED, BCM27 aux marker). Fail-safe,
        # may be None in tests. The aux marker (a laser) is opt-in.
        self._status_led = status_led
        self._aux_marker = aux_marker
        self._aux_enabled = cfg.app.aux_marker_enabled

    def _update_indicators(self) -> None:
        state = self.sm.state
        if self._status_led is not None:
            self._status_led.set(state is not FireState.SAFE)
        if self._aux_marker is not None:
            self._aux_marker.set(self._aux_enabled
                                 and state in (FireState.AIMING, FireState.FIRING))

    def tick(self, tracks: Sequence[Track]) -> Telemetry:
        tracks = list(tracks)
        if not tracks:
            self.selector.select([])
            self.sm.step(FireContext(has_target=False))
            self._update_indicators()
            return Telemetry(self.sm.state, 0, None, float("inf"), None,
                             self.servo.last_angle(Axis.PAN),
                             self.servo.last_angle(Axis.TILT), False, False)

        by_id: Dict[int, Track] = {t.id: t for t in tracks}
        scored = [(t.id, score_track(t, self.cfg.killzone, self.cfg.strategy,
                                     self._frame_w, self._frame_h)) for t in tracks]
        target_id = self.selector.select(scored)
        target = by_id.get(target_id)
        if target is None:
            self.sm.step(FireContext(has_target=False))
            self._update_indicators()
            return Telemetry(self.sm.state, len(tracks), None, float("inf"), None,
                             self.servo.last_angle(Axis.PAN),
                             self.servo.last_angle(Axis.TILT), False, False)

        # Predict the lead point and aim there (calibration feed-forward).
        px, py = predict_lead(target, self.cfg.predict.lead_time_s, self.cfg.predict.fps)
        pan_t, tilt_t = apply_aim_offsets(
            *apply_calibration(self.cal, px, py),
            parallax_pan_deg=self.cfg.aim.parallax_pan_deg,
            drop_tilt_deg=self.cfg.aim.drop_tilt_deg,
        )
        max_step = self.cfg.controller.max_step_deg
        pan_cmd = self.servo.set_angle(
            Axis.PAN, slew_toward(self.servo.last_angle(Axis.PAN), pan_t, max_step))
        tilt_cmd = self.servo.set_angle(
            Axis.TILT, slew_toward(self.servo.last_angle(Axis.TILT), tilt_t, max_step))

        aim_error_px = distance_to_center_px(px, py, self.cfg.killzone)
        in_kz = is_in_kill_zone(px, py, self.cfg.killzone)
        self.sm.step(FireContext(has_target=True, aim_error_px=aim_error_px,
                                 predicted_in_killzone=in_kz))
        self._update_indicators()

        return Telemetry(
            state=self.sm.state, num_tracks=len(tracks), selected_target_id=target_id,
            aim_error_px=aim_error_px, predicted_xy=(px, py),
            pan_cmd_deg=pan_cmd, tilt_cmd_deg=tilt_cmd,
            in_killzone=in_kz, would_fire=self.sm.last_would_fire,
        )
