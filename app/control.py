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

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

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

logger = logging.getLogger(__name__)


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
                 status_led=None, aux_marker=None, pump=None):
        self.cfg = cfg
        self.servo = servo
        self.selector = selector
        self.sm = state_machine
        self._pump = pump          # optional Pump for the manual fire override
        self.cal = calibration or Calibration.from_config(cfg.aim)
        self._frame_w = float(cfg.camera.capture_width_px)
        self._frame_h = float(cfg.camera.capture_height_px)
        # Optional indicators (BCM23 status LED, BCM24 aux marker). Fail-safe,
        # may be None in tests. The aux marker (a laser) is opt-in.
        self._status_led = status_led
        self._aux_marker = aux_marker
        self._aux_enabled = cfg.app.aux_marker_enabled
        # Manual marker override (boresight/calibration): forces BCM24 on now,
        # independent of fire-state. None of the auto behaviour applies while set.
        self._marker_on = False
        self._last_selected: Optional[int] = None

    def apply_config(self) -> None:
        """Re-apply config that was snapshotted at construction.

        Most tunables (killzone, strategy weights, predict, controller, fire) are
        read live from ``self.cfg`` each tick, so mutating them takes effect on the
        next tick with no action here. These few are captured once at init and need
        an explicit refresh after a live config change (e.g. from the web UI).
        """
        self.cal = Calibration.from_config(self.cfg.aim)
        self._frame_w = float(self.cfg.camera.capture_width_px)
        self._frame_h = float(self.cfg.camera.capture_height_px)
        self._aux_enabled = self.cfg.app.aux_marker_enabled
        self.selector.hysteresis = self.cfg.strategy.switch_hysteresis
        self.selector.min_dwell = self.cfg.strategy.min_target_dwell_frames

    def manual_fire(self) -> bool:
        """Operator-commanded fire: pulse the pump for ``fire.fire_duration_s`` NOW,
        regardless of arm state / target / kill-zone (the auto interlock is bypassed
        on purpose — this is the manual trigger). Non-blocking: the pump self-times
        OFF, so it can never latch on. Returns False if no pump is wired.
        """
        if self._pump is None:
            return False
        self._pump.fire(self.cfg.fire.fire_duration_s)
        logger.info("MANUAL FIRE (%.1fs, state=%s)",
                    self.cfg.fire.fire_duration_s, self.sm.state.value)
        return True

    def manual_pump_off(self) -> bool:
        """Force the pump OFF immediately (cancels a manual/auto pulse)."""
        if self._pump is None:
            return False
        self._pump.off()
        return True

    def set_marker(self, on: bool) -> bool:
        """Manually force the aux laser marker on/off (boresight/calibration).

        Drives BCM24 immediately and latches; ``on`` overrides the auto behaviour,
        ``off`` returns to auto (which is OFF unless armed + aiming + opt-in). Reset
        on disarm. Fail-safe: a marker error never propagates.
        """
        self._marker_on = bool(on)
        if self._aux_marker is not None:
            self._aux_marker.set(self._marker_value(self.sm.state))
        logger.info("aux marker %s (manual)", "ON" if on else "off")
        return self._marker_on

    def _marker_value(self, state: "FireState") -> bool:
        return self._marker_on or (self._aux_enabled
                                   and state in (FireState.AIMING, FireState.FIRING))

    def _update_indicators(self) -> None:
        state = self.sm.state
        if self._status_led is not None:
            self._status_led.set(state is not FireState.SAFE)
        if self._aux_marker is not None:
            self._aux_marker.set(self._marker_value(state))

    def _note_target(self, target_id: Optional[int]) -> None:
        """Log target acquisition / switch / loss at INFO (edges only, no spam)."""
        if target_id == self._last_selected:
            return
        if target_id is None:
            logger.info("target lost (#%s)", self._last_selected)
        elif self._last_selected is None:
            logger.info("target acquired #%s", target_id)
        else:
            logger.info("target switch #%s -> #%s", self._last_selected, target_id)
        self._last_selected = target_id

    def tick(self, tracks: Sequence[Track]) -> Telemetry:
        tracks = list(tracks)
        armed = self.sm.state is not FireState.SAFE
        pan_now, tilt_now = self.servo.last_angle(Axis.PAN), self.servo.last_angle(Axis.TILT)
        if not tracks:
            self.selector.select([])
            self.sm.step(FireContext(has_target=False))
            self._note_target(None)
            self._update_indicators()
            return Telemetry(self.sm.state, 0, None, float("inf"), None,
                             pan_now, tilt_now, False, False)

        by_id: Dict[int, Track] = {t.id: t for t in tracks}
        scored = [(t.id, score_track(t, self.cfg.killzone, self.cfg.strategy,
                                     self._frame_w, self._frame_h)) for t in tracks]
        target_id = self.selector.select(scored)
        target = by_id.get(target_id)
        if target is None:
            self.sm.step(FireContext(has_target=False))
            self._note_target(None)
            self._update_indicators()
            return Telemetry(self.sm.state, len(tracks), None, float("inf"), None,
                             pan_now, tilt_now, False, False)

        self._note_target(target_id)
        # Predict the lead point and aim there (calibration feed-forward).
        px, py = predict_lead(target, self.cfg.predict.lead_time_s, self.cfg.predict.fps)
        pan_t, tilt_t = apply_aim_offsets(
            *apply_calibration(self.cal, px, py),
            parallax_pan_deg=self.cfg.aim.parallax_pan_deg,
            drop_tilt_deg=self.cfg.aim.drop_tilt_deg,
        )
        # Servos move ONLY when armed. Disarmed (SAFE) freezes the turret so manual
        # jog / Center hold and it never chases false positives — telemetry still
        # reports where it *would* aim.
        if armed:
            max_step = self.cfg.controller.max_step_deg
            pan_cmd = self.servo.set_angle(
                Axis.PAN, slew_toward(pan_now, pan_t, max_step))
            tilt_cmd = self.servo.set_angle(
                Axis.TILT, slew_toward(tilt_now, tilt_t, max_step))
        else:
            pan_cmd, tilt_cmd = pan_now, tilt_now

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
