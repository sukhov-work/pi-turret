# mem:decisions/control_and_persistence — disarm semantics, live-config mutation, overlay persistence

Decided 2026-06-29 during first-on-Pi bring-up. Durable design rules; code is authority
(`app/control.py`, `app/web.py`, `config.py`, `main.py`). Related: `mem:core`, `mem:architecture/v2_scaffold`.

## Arming / servo motion
- **DISARM (SAFE) freezes the servos.** `ControlLoop.tick` only calls `servo.set_angle` when
  `sm.state is not FireState.SAFE`; telemetry still reports the would-aim point. So Center + manual
  jog HOLD, and the turret never chases false positives while disarmed. (Before: it slewed even when SAFE.)
- **Boot is DISARMED.** `main.py` calls `control.sm.enter_safe()` before `pipeline.start()` — no
  slew until the operator presses Arm. `fire.enabled=False` is the separate SAFE-fire default.
- `servo.center()`/boot home = `servo.home_pan_deg/home_tilt_deg` (geometric mid 26/15 by default;
  the operator overwrites it with **Set Home** = current pose, then **Save**).

## Aux laser marker
- `ControlLoop.set_marker(bool)` is a **manual override** (boresight/calibration): forces BCM27 now,
  independent of state; `_marker_value = _marker_on or (aux_enabled and state in AIMING/FIRING)`.
  Web `marker_on/marker_off`. Reset on full disarm (`main.disarm`). Laser-safety default off.

## Live config: mutate-in-place vs atomic-swap (subtle)
- `web.update_config` does an **atomic whole-section swap** for the editable sections — safe because
  those tunables are re-read from `self.cfg` each tick.
- BUT the **servo, aim (calibration), and camera** sections are held by long-lived objects
  (`ServoController._cfg`, `Calibration` via `apply_config`, `PiCamCapture._cfg`). A swap wouldn't
  reach them. So calibration actions **mutate those dataclasses in place** (`web._set_home/_set_limits/
  _set_rotation/_cal_fit`) and call `control.apply_config()` where needed. `camera.rotation_deg` is
  read per-frame in `PiCamCapture.read_frame`, so setting it is live (no restart).

## Persistence — `config.local.yaml` overlay (git-ignored, per box)
- `load_config` = typed defaults ← `config.yaml` (documented base) ← `config.local.yaml` (machine
  overlay), **per-key** merge. `save_local_config` writes only the **delta vs base** (keeps base
  comments + lets later base edits affect untouched keys). Web **Save Config** + needs PyYAML on the
  box. This is how the **calibrated home / limits / aim coeffs / rotation** survive reboot.
