# mem:decisions/control_and_persistence — disarm semantics, live-config, overlay persistence, manual fire

Decided 2026-06-29 during first-on-Pi bring-up (two rounds). Durable design rules; code is
authority (`app/control.py`, `app/web.py`, `config.py`, `main.py`). Param reference:
`claude-docs/PARAMETERS.md` (long form of the UI ⓘ tooltips / `web_ui.html::PARAM_DOCS`).
Related: `mem:core`, `mem:architecture/v2_scaffold`.

## Arming / servo motion
- **DISARM (SAFE) freezes the servos.** `ControlLoop.tick` only calls `servo.set_angle` when
  `sm.state is not FireState.SAFE`; telemetry still reports the would-aim point. So Center + manual
  jog HOLD, and the turret never chases false positives while disarmed.
- **Boot is DISARMED.** `main.py` calls `control.sm.enter_safe()` before `pipeline.start()` — no
  slew until the operator presses Arm. `fire.enabled=False` is the separate SAFE-fire default.
- `servo.center()`/boot home = `servo.home_pan_deg/home_tilt_deg` (geometric mid 26/15 by default;
  overwritten by **Set Home** = current pose, then **Save**).
- **Manual slew direction is physical:** on this rig a HIGHER tilt angle points the barrel DOWN, so
  `_JOG` "up" = tilt −step (raises aim), "down" = +step. Pan: +deg = left.

## Fire
- **Auto** fire is gated by `fire.enabled` + the state machine (aim-error + kill-zone predicates).
- **Manual FIRE** (`ControlLoop.manual_fire`, web `fire_now`, red UI button) pulses the pump for
  `fire.fire_duration_s` in **any** state — the auto interlock is bypassed by design. Non-blocking
  (pump self-times OFF, can't latch); `manual_pump_off`/`pump_off` force-stops. Pump injected into
  `ControlLoop` in `main.py`.
- **Aux laser marker:** `ControlLoop.set_marker(bool)` manual override (boresight), independent of
  state; auto path lights only when `aux_marker_enabled` + state in AIMING/FIRING. Reset on disarm.

## Live config: every section editable, mutate-vs-swap, re-sync (subtle)
- **All 14 sections are UI-editable + persistable** (`EDITABLE_SECTIONS = tuple(_SECTIONS)`).
- `update_config` does an **atomic whole-section swap** (build new instance → validate → swap →
  rollback on fail) so the control thread never reads a torn section.
- **The catch:** objects that snapshot a dataclass ref (ServoController, IouTracker, PiCamCapture,
  CoralDetector) wouldn't see a swap. So each got an **`apply_config(section_cfg)`** and
  `TurretWebController._resync()` re-points them all after every swap (control loop + servo + the
  pipeline-held tracker/capture/detector). Edits take effect next tick where safe.
- **Restart-only fields** (model_path/input_size/backend, capture dims/format/detection_source,
  i2c/pins/pwm_freq, ports) persist + apply on reboot — flagged `[restart]` in `PARAM_DOCS`/glossary.
- Calibration handlers still mutate in place (`_set_home/_set_limits/_set_rotation/_cal_fit`);
  `camera.rotation_deg` is read per-frame so it's live. `detector.conf/iou/num_classes/coords_normalized`
  apply on the next infer.
- **Aim is calibrated with a LINEAR affine** (`aim.pan_coeffs/tilt_coeffs` = a,b,c per axis) + two
  constant trims (`parallax_pan_deg` L/R nozzle gap, `drop_tilt_deg` gravity hold-over). Sufficient
  for the v2 model; no lens-distortion term (add edge calibration points) and drop is range-constant.

## Persistence — `config.local.yaml` overlay (git-ignored, per box)
- `load_config` = typed defaults ← `config.yaml` (documented base) ← `config.local.yaml` (machine
  overlay), **per-key** merge. `save_local_config` writes only the **delta vs base** (keeps base
  comments; later base edits affect untouched keys). Web **Save Config**; needs PyYAML on the box
  (verified present on the Pi). This is how calibrated home/limits/aim coeffs/rotation survive reboot.
