# pi-turret v2 — Parameters Glossary

Canonical reference for **every tunable**. The terse ⓘ tooltips in the web UI
(`app/web_ui.html` → `PARAM_DOCS`) are the short form of this; this doc is the long
form. Defaults are the dataclass defaults in `config.py` (and the committed
`config.yaml`). If they ever disagree, **the code wins** — re-read `config.py`.

## How parameters live, apply, and persist
- **Layered load** (`config.py::load_config`): typed defaults ← `config.yaml` (the
  documented base) ← `config.local.yaml` (the machine-written overlay, git-ignored,
  per box). Per-key merge, so editing the base still affects keys the overlay didn't touch.
- **Edit + apply (live):** the web UI **Parameters** panel exposes **every** section.
  An edit is validated (`Config.validate`, with rollback) and re-synced into the live
  objects (`TurretWebController._resync` → `apply_config` on the control loop, servo,
  tracker, capture, detector) so it takes effect **on the next tick** — where safe.
- **`[restart]`** on a field below = it persists but only takes effect on the next
  `python3 main.py` (it's read once at construction: a model, a GPIO/I2C binding, the
  picamera2 stream geometry, a server port).
- **Persist:** **Save Config** writes only the **delta vs base** to `config.local.yaml`
  (atomic). Calibrated home / limits / aim coeffs / rotation survive reboot this way.
  Needs PyYAML on the box.
- **`[safety]`** = changing it can damage hardware or fire water; understand it first.

## The flow (where each section acts)
```
Pi Camera (camera) ─► detect (detector) ─► track (tracker) ─► predict lead (predict)
   ─► score + select target (strategy, killzone) ─► pixel→angle (aim) + offsets
   ─► slew rate-limit (controller) ─► clamp + write (servo) ─► fire decision
   (fire state machine, killzone) ─► pump.  USB webcam (stream) = human view only.
   app/pump/remote = system, GPIO, operator I/O.
```
**Coordinate space:** all targeting math — detections, tracks, kill-zone, calibration,
the tactical canvas — is in the **full-frame 1152×1152 px** space (`camera.capture_*`),
even though inference runs on the smaller `detector.input_size_px` lores frame (the
detector rescales its output up). `killzone` 576,576 = frame center.

---

## camera — the detection camera (Pi Camera Module 3 / IMX708)
The Pi Camera is the **detection** source (never streamed to the operator). It runs a
small greyscale lores plane for the detector + a full-res main stream that defines the
coordinate space.

| Param | Default | Description |
|---|---|---|
| `detection_source` | `picamera2` | `[restart]` Which camera feeds detection. The USB webcam is the human stream and is **never** detection. Leave `picamera2`. |
| `capture_width_px` | `1152` | `[restart]` Full-frame coordinate-space width. **All** aiming math, the kill-zone, calibration and the canvas use this space. Change it → every pixel coord rescales → **re-calibrate**. |
| `capture_height_px` | `1152` | `[restart]` Full-frame height (square frame). |
| `lores_format` | `YUV420` | `[restart]` Low-res detection-plane format — **must be YUV420 on Pi 4** (RGB lores is Pi 5 only). The detector reads the Y (luma) plane as greyscale. |
| `fixed_focus` | `true` | `[restart]` Lock focus (no AF hunting on a moving bird) at `lens_position`. Recommended for a turret. |
| `lens_position` | `4.0` | Manual focus in **dioptres** (1/m): 4.0 ≈ 0.25 m; larger = nearer. Tune to engagement range for sharp detections. Applies at start. |
| `rotation_deg` | `0` | Software rotation of the detection frame (`0/90/180/270`) to correct a physically rotated module. **Read per-frame → applies live** (cam-rot buttons). Confirmed `0` on this rig (image already upright). Set this **before** calibrating — rotating after a fit invalidates it. |

## detector — Edge-TPU bird detector (anchor-free YOLOv8 @256, INT8)
Runs on the Coral USB. Single-class. Output `[1,5,N]`, decoded with **no objectness /
no YOLOv5 anchors** (that decoder was the v1 bug).

| Param | Default | Description |
|---|---|---|
| `backend` | `coral_yolo` | `[restart]` `coral_yolo` (Edge-TPU YOLOv8, default) \| `coral_mobiledet` (fallback) \| `cpu`. Selects the loader. |
| `model_path` | `models/bird_yolov8n_256_int8_edgetpu_run1.tflite` | `[restart]` The trained detector. **Must end `_edgetpu.tflite`** or it silently runs on CPU (Coral bypassed). Loaded once. |
| `input_size_px` | `256` | `[restart]` Square model input; the lores frame is resized to it, then detections rescale to `camera.capture_width_px`. Baked into the compiled model — must match. |
| `num_classes` | `1` | Decode output width `[1,4+nc,N]`. Applies next infer. Must match the model. |
| `conf_threshold` | `0.25` | Min detection confidence (0–1) to keep a box. ↑ = fewer false positives but misses faint/distant birds; ↓ = more noise for the tracker to filter. **Live.** |
| `iou_threshold` | `0.5` | NMS overlap (0–1): boxes overlapping more than this merge to one. ↓ = aggressive de-dup; ↑ = may keep duplicates. **Live.** |
| `coords_normalized` | `true` | Model emits box xywh in [0,1] (`true`) vs input-px. **Pinned `true`** by the golden fixture for these Ultralytics v8 edgetpu exports — wrong = boxes off by ~1000 px (the v1 bug). Don't change without re-verifying against a fixture. |

## tracker — greedy-IoU + constant-velocity multi-object tracker
Gives stable integer IDs and a velocity estimate (px/frame) that feeds the lead predictor.

| Param | Default | Description |
|---|---|---|
| `iou_match_threshold` | `0.3` | Min box overlap (IoU 0–1) to link a detection to a track. ↑ = stricter (more new IDs, breaks under fast motion); ↓ = looser (risks merging two birds). |
| `max_age_frames` | `30` | Frames a track survives **without** a match before being dropped — i.e. how long it coasts through an occlusion keeping its ID so the predictor can extrapolate. ↑ = persistence but ghosts linger. |
| `min_hits` | `3` | Detections before a track is **confirmed** and targetable. ↑ = fewer false tracks from one-frame blips but slower to engage a real new bird. Opposes `max_age_frames` (confirm vs retain). |
| `velocity_smoothing` | `0.5` | EMA factor (0–1) for the velocity estimate. →1 = fast but noisy; →0 = smooth but laggy. Feeds `predict` → affects lead accuracy on maneuvering targets. |

## predict — aim-ahead (lead)
Shifts the aim point to where the target **will be** when the water arrives.

| Param | Default | Description |
|---|---|---|
| `lead_time_s` | `0.45` | Seconds to aim ahead ≈ servo travel + water time-of-flight. Multiplied by track velocity (px/s). Too high overshoots fast movers; too low trails them. Pairs with `controller.max_step_deg` (slower slew ⇒ more lead). |
| `fps` | `20.0` | Frames/s used **only** to convert tracker velocity (px/**frame**) → px/**s**. Set to the **measured detection FPS** (~59 on this rig) or the lead distance is wrong. Not the camera/stream fps — the inference loop's rate. |

## strategy — target scoring + switching (when multiple birds)
Each track gets a weighted score; the top scorer is engaged; switching needs to clear a
margin so the turret doesn't dither.

| Param | Default | Description |
|---|---|---|
| `w_killzone` | `1.0` | Weight: proximity to kill-zone center (distance ÷ frame diagonal). ↑ = prefer targets already where you can hit them. |
| `w_size` | `0.5` | Weight: box size/closeness. ↑ = prefer large/near birds. |
| `w_dwell` | `0.3` | Weight: track persistence (hits), saturating at `dwell_norm_frames`. ↑ = prefer established tracks over flicker. |
| `w_approach` | `0.7` | Weight: closing velocity toward the kill-zone. ↑ = prioritize incomers over outgoers. |
| `w_confidence` | `0.4` | Weight: detector box score. ↑ = prefer confident detections (fewer false engagements). |
| `dwell_norm_frames` | `20` | Hit count at which the dwell score saturates. Defines "established" for `w_dwell`. |
| `switch_hysteresis` | `0.15` | Score margin a **new** candidate must beat the **current** target by before switching. Anti-dither; pairs with `min_target_dwell_frames`. Too low = twitchy. |
| `min_target_dwell_frames` | `5` | Minimum frames to **hold** the current target before any switch — time-based anti-flap (the score-based twin is `switch_hysteresis`). |

## killzone — the engagement zone (frame pixels)
Where a predicted target must land for an auto-shot, and the reference aim-error is
measured from.

| Param | Default | Description |
|---|---|---|
| `shape` | `rect` | `rect` (uses `half_w/half_h`) \| `circle` (uses `radius`). |
| `cx_px` | `576.0` | Center X (px); 576 = center of the 1152 frame. Targets scored partly by proximity to it (`strategy.w_killzone`); aim error = distance from the predicted point to here. |
| `cy_px` | `576.0` | Center Y (px). Move (cx,cy) to bias engagement to a screen region (e.g. a feeder). |
| `half_w_px` | `120.0` | Half-width of the **rect** zone (full = 2×). ↑ = easier "in kill-zone" (more shots, less precise). Ignored when `shape=circle`. |
| `half_h_px` | `120.0` | Half-height of the **rect** zone. |
| `radius_px` | `120.0` | Radius of the **circle** zone. Used only when `shape=circle`. |

## aim — pixel→angle calibration + manual trims  ⟵ **calibrate aim here**
The fitted transform from a target's frame pixel to a servo angle, plus two physical
offset trims. Set the coeffs with the **Calibrate** card (click points → Fit), then trim.

| Param | Default | Description |
|---|---|---|
| `pan_coeffs` | `[-0.04, 0.0, 59.04]` | Affine map `pan_deg = a·cx + b·cy + c`. Horizontal pixel → pan angle. From Calibrate→Fit (least-squares). `b`≈0 unless the camera is rolled. Output clamped to `servo.pan_min/max`. |
| `tilt_coeffs` | `[0.0, 0.0667, -10.4]` | Affine `tilt_deg = a·cx + b·cy + c`; `a`≈0 normally. From Fit. Output clamped to `servo.tilt_min/max`. Biased high/low everywhere? prefer `drop_tilt_deg` over re-fitting. |
| `parallax_pan_deg` | `0.0` | Constant pan offset (deg) for the horizontal **camera↔nozzle** gap (they aren't co-located). Trim if shots land consistently left/right. Range-dependent. |
| `drop_tilt_deg` | `0.0` | Constant "aim above" (deg) for water-jet gravity drop. ↑ = aim higher (longer range). With `parallax_pan_deg`, the two manual trims layered on the fit — use these for first hits before re-fitting. |

**Sufficiency note:** for the v2 **linear-affine** model these knobs (+`predict`,
`controller.max_step_deg`, `servo` clamps/home, `fire.aim_deadband_px`, `killzone`) are
complete. Two boundaries: the map is **linear** (no lens-distortion term — if frame
**edges** aim off, add calibration points toward the edges), and `drop_tilt_deg` is a
**constant** (tune per engagement range; it doesn't auto-scale with distance).

## controller — slew rate-limit (+ optional PI trim)
The default aim path is calibration feed-forward + a per-tick slew cap. The PI fields
are for the **alternative** pixel-centering trim mode (not the default path).

| Param | Default | Description |
|---|---|---|
| `kp` | `0.02` | `[PI-trim only]` Proportional gain (deg per px error). Default aim path ignores it. Too high oscillates. |
| `ki` | `0.0` | `[PI-trim only]` Integral gain (deg per px·tick). Removes steady bias, risks windup; 0 disables. Bounded by `integral_limit_deg`. |
| `deadband_px` | `8.0` | `[PI-trim only]` Error below which the PI controller does nothing (ignore jitter). Distinct from `fire.aim_deadband_px` (firing gate). |
| `max_step_deg` | `6.0` | **Per-tick slew cap (deg)** on the default AND PI paths — the servo moves ≤ this per control tick. Bounds current spikes / overshoot / gear strain; smooths motion. ↓ = smoother but slower to settle (compensate with `predict.lead_time_s`). |
| `integral_limit_deg` | `5.0` | `[PI-trim only]` Anti-windup clamp on the integral's contribution. Relevant when `ki>0`. |
| `backlash_takeup_deg` | `1.0` | Final-approach overshoot (deg) so the servo settles from one direction, taking up MG996R gear backlash for repeatable aim. Effect is rig-specific (verify on the Pi). |

## servo — actuation + safety clamps  `[safety]`
Single owner of motion. **Every** write is clamped to the mechanical envelope, then the
pulse is clamped to the absolute guard. PCA9685 over I2C.

| Param | Default | Description |
|---|---|---|
| `i2c_bus` | `1` | `[restart]` PCA9685 I2C bus (Pi = 1). Used only at driver init. |
| `i2c_address` | `0x40` | `[restart]` PCA9685 address (0x40=64). Shares the bus with the LCD (different address). |
| `pwm_freq_hz` | `50` | `[restart]` Servo PWM frequency (50 Hz for analog MG996R). Wrong value desyncs pulse↔angle. |
| `pan_channel` | `1` | PCA9685 channel for **pan**. Swapping pan/tilt channels swaps the axes. |
| `tilt_channel` | `0` | PCA9685 channel for **tilt**. |
| `pan_min_deg` / `pan_max_deg` | `5.0` / `47.0` | `[safety]` Mechanical pan envelope — every pan command is clamped here. Set to the rig's real limits so auto-aim can't hit a hard stop. Use **Calibrate → Travel limits** (disarmed). `min < max`. |
| `tilt_min_deg` / `tilt_max_deg` | `5.0` / `25.0` | `[safety]` Mechanical tilt envelope. Too wide risks the mount/ground/sky. |
| `pulse_slope_us_per_deg` | `11.111` (2000/180) | `[safety]` Angle→pulse: `pulse_us = deg·slope + offset` — v1's verified MG996R calibration. Wrong values mis-point **every** command. Re-characterize only with care; verify on the Pi. |
| `pulse_offset_us` | `501.0` | `[safety]` Offset of the angle→pulse map (gives ~556–1023 µs over travel). |
| `pulse_min_us` / `pulse_max_us` | `500.0` / `2500.0` | `[safety]` Absolute pulse guard (µs) — the final net; every output pulse is clamped here regardless of angle. The deg clamps should bind first. Don't narrow below ~556 µs or you cut travel. |
| `home_pan_deg` / `home_tilt_deg` | `26.0` / `15.0` | The **Center**/boot rest pose. Centers here at startup and on Center. Set your true forward pose with **Calibrate → Set Home** (records the jogged angle), then **Save**. (Geometric mid-travel by default; calibrate to the real pose.) |

## fire — fire control + manual trigger  `[safety]`
Non-blocking: pump on → timer → off → COOLDOWN, tracking throughout.

| Param | Default | Description |
|---|---|---|
| `enabled` | `false` | Master **auto**-fire arm. `false` = track/aim + report "would fire" but never auto-actuate (safe demo). `true` = the pump **will** run when the fire predicate is met. The manual **FIRE** button is independent of this. |
| `fire_duration_s` | `1.0` | How long one shot runs the pump (s), for auto **and** the manual FIRE button. A timer turns it off (non-blocking). |
| `cooldown_s` | `2.0` | Forced wait after a shot before firing again (debounce). State stays COOLDOWN this long. |
| `aim_deadband_px` | `12.0` | Max aim error (px, predicted point → kill-zone center) to take an **auto** shot — the precision gate. Smaller = only well-aimed shots. Distinct from `controller.deadband_px` (motion). |
| `require_killzone` | `true` | If true, an auto-shot also needs the **predicted** position inside the kill-zone (not just small aim error). `false` = fire on aim-error alone. |

> **Manual FIRE** (red button / `fire_now` cmd) pulses the pump for `fire_duration_s` in
> **any** state (the auto interlock is bypassed by design); it's a momentary pulse so it
> can't latch on. `pump_off` force-stops.

## pump — firing + indicator GPIO  `[safety]`  (reuses v1 pins — do not rewire)
| Param | Default | Description |
|---|---|---|
| `pump_gpio_bcm` | `26` | `[restart][safety]` BCM pin to the water-pump relay/MOSFET (v1's "main laser" pin). **Always** via relay/MOSFET + flyback diode, never a bare GPIO. Binds at start. |
| `aux_gpio_bcm` | `24` | `[restart]` BCM pin for the aux laser/marker (rewired to its own pin, was v1's BCM27). Drives the opt-in/manual aim marker. |
| `status_led_gpio_bcm` | `23` | `[restart]` BCM pin for the status LED (v1). Lit while ARMED. |
| `active_high` | `true` | `[restart][safety]` Relay energizes on HIGH (`true`) or LOW. Wrong polarity = pump on when it should be off. Verify — it controls water. |

## app — system + behaviour
| Param | Default | Description |
|---|---|---|
| `annotation_mode` | `off` | File-based annotation of **saved** frames: `off` \| `fire_frames_only` \| `full_video` (dataset/debug to disk). Distinct from the live DET-CAM video toggle. |
| `snapshot_mode` | `off` | Auto-save snapshots for the training flywheel: `off` \| `every` \| `fire_only` \| `sampled` → `snapshot_dir`. |
| `snapshot_sample_every` | `30` | When `sampled`, save 1 of every N frames. |
| `snapshot_dir` | `dataset` | Where snapshots are written. |
| `detection_mode` | `full_frame` | `full_frame` (detect every frame, verified) \| `motion_gated` (skip static frames — seam, not active). |
| `stream_source` | `usb` | Operator view: `usb` (mjpg-streamer webcam) \| `picam_annotated` (debug). The DET-CAM **video** button is the separate live detection view. |
| `web_port` | `8001` | `[restart]` Control-UI port. Startup log prints every reachable URL. |
| `log_level` | `INFO` | `INFO` = flow (state changes, FIRE, target acquire/switch/lost). `DEBUG` = verbose per-tick. |
| `detection_video_enabled` | `false` | Master gate for the DET-CAM debug JPEG (raw lores frame under the canvas overlay). Off by default — encode competes with inference (~1% of a core at 5 fps, measured). The UI **video** button toggles it. |
| `detection_video_max_fps` | `5.0` | Server-side encode-rate cap so a fast browser can't starve detection. |
| `detection_video_quality` | `70` | JPEG quality (1–100) for the debug stream. |
| `lcd_enabled` | `true` | Drive the 1602A I2C LCD (boot/IP, state, target, aim err, fps, shots). Fail-safe — LCD errors never stop control. |
| `lcd_refresh_hz` | `4.0` | LCD re-render rate; deliberately low so the slow I2C write (own thread) never blocks control. |
| `status_led_enabled` | `true` | Drive the BCM23 status LED (lit while ARMED/not-SAFE). |
| `aux_marker_enabled` | `false` | Allow the BCM24 aux laser to **auto**-light as an aim marker during AIMING/FIRING (opt-in, laser safety). The manual Marker On/Off buttons override this regardless. |

## stream — USB-webcam MJPEG (human spotter view, separate process)
A `mjpg-streamer` subprocess does UVC hardware-MJPEG passthrough so the Pi spends **no
detection compute** on rendering. Independent of detection.

| Param | Default | Description |
|---|---|---|
| `enabled` | `true` | Whether the stream may run at all. |
| `device` | `/dev/video0` | V4L2 node of the **USB webcam** (confirmed `video0` = UVC cam, not the Pi Cam). |
| `width_px` / `height_px` / `fps` | `640` / `480` / `15` | Webcam stream geometry. Higher = clearer but more USB bandwidth/CPU in the streamer process; **does not** affect detection. Must be a supported mode. |
| `port` | `8080` | HTTP port for the MJPEG (separate from `app.web_port`). The UI's USB-feed `<img>` hits this. |
| `binary` | `v1/mjpg-streamer/.../_build/mjpg_streamer` | Path to the (committed ARM) `mjpg_streamer` exe. The bare name isn't on PATH → wrong path is the `FileNotFoundError`. |
| `plugin_dir` | `v1/mjpg-streamer/mjpg-streamer-experimental` | Holds `input_uvc.so`/`output_http.so` (added to `LD_LIBRARY_PATH`). |
| `input_plugin` / `output_plugin` | `input_uvc.so` / `output_http.so` | UVC grab (low CPU) / HTTP serve. Rarely changed. |
| `www_dir` | `""` | Optional web root for mjpg-streamer's own pages (`""` = stream only). |

## remote — IR remote control (PROPOSED seam — additive hardware)
v1 has no GPIO inputs; needs an IR receiver wired + a `dtoverlay`. Off until fitted.

| Param | Default | Description |
|---|---|---|
| `enabled` | `false` | Whether the IR listener runs. |
| `gpio_bcm` | `17` | `[restart]` Proposed free pin for the IR receiver. Needs `/boot/config.txt dtoverlay=gpio-ir`. Confirm before wiring. |
| `input_device` | `""` | evdev path (`/dev/input/eventN`) the receiver appears as (from `ir-keytable`). |
| `key_toggle_arm` | `KEY_POWER` | IR key that arms/disarms. |
| `key_enable_fire` | `KEY_OK` | IR key that toggles **auto**-fire. |
| `key_center` | `KEY_HOME` | IR key that centers to home. |
| `key_pan_left` / `key_pan_right` | `KEY_LEFT` / `KEY_RIGHT` | IR keys that jog pan. |
| `key_tilt_up` / `key_tilt_down` | `KEY_UP` / `KEY_DOWN` | IR keys that jog tilt. |

---

## Calibrating aim — quick procedure
1. **Disarm** (servos freeze; you're the only mover). Enable **DET-CAM video** to see the frame.
2. Set `camera.rotation_deg` first if the image isn't upright (cam-rot buttons).
3. **Travel limits:** jog to the safe extremes; set `servo.pan/tilt_min/max` (Calibrate → Travel limits).
4. **Home:** jog to forward-level; **Set Home** → `servo.home_*`.
5. **Transform:** jog so the barrel points at a fixed target; **Pick** and click that target on
   DET-CAM; repeat at **≥3 spread** points (toward the edges helps the linear fit); **Fit & Apply**
   → `aim.pan_coeffs/tilt_coeffs`.
6. **Trim:** dial `aim.parallax_pan_deg` (L/R) and `aim.drop_tilt_deg` (gravity) for first hits.
7. **Tune the fire gate:** `fire.aim_deadband_px`, `fire.require_killzone`, `killzone.*`.
8. **Save Config** → persists to `config.local.yaml` (survives reboot).
