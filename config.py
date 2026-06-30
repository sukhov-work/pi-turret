"""Typed configuration for pi-turret v2 — the single source of truth for tunables.

Every magic number lives here (clamps, pulse band, thresholds, calibration, GPIO).
``Config`` is built from nested dataclasses; ``load_config`` reads ``config.yaml``
over the defaults. No hardware-specific numbers are hard-coded elsewhere.

Defaults encode the v1 as-built hardware map (PCA9685 @ I2C bus 1 / 0x40, pan ch1,
tilt ch0, BCM26 pump) so v2 talks to the same rig; aiming starts from the v1 preset.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from errors import ConfigError


@dataclass
class CameraConfig:
    detection_source: str = "picamera2"      # picamera2 (Pi Cam) | usb
    capture_width_px: int = 1152             # v1 square capture (3x384)
    capture_height_px: int = 1152
    lores_format: str = "YUV420"             # Pi 4 lores MUST be YUV420
    fixed_focus: bool = True                 # no AF hunting on a moving target
    lens_position: float = 4.0               # diopters; tune to engagement range
    # Software correction for a physically rotated module (e.g. FPC ribbon exits
    # to the side). 0/90/180/270 only; applied to the detection frame at capture
    # (np.rot90) so the whole pipeline shares one consistent pixel space. Prefer a
    # physical remount (ribbon at the bottom); this is the fallback. Square frames
    # so dims are unchanged.
    rotation_deg: int = 0


@dataclass
class DetectorConfig:
    backend: str = "coral_yolo"              # coral_yolo | coral_mobiledet | cpu
    model_path: str = "models/bird_yolov8n_256_int8_edgetpu_run1.tflite"
    input_size_px: int = 256                 # YOLOv8n@256 primary; MobileDet uses 320
    num_classes: int = 1                     # single-class "bird"
    conf_threshold: float = 0.25
    iou_threshold: float = 0.5
    coords_normalized: bool = True           # golden-fixture pinned: Ultralytics v8 tflite emits normalized xywh


@dataclass
class TrackerConfig:
    iou_match_threshold: float = 0.3
    max_age_frames: int = 30                 # keep a lost track this long (occlusion)
    min_hits: int = 3                        # frames before a track is "confirmed"
    velocity_smoothing: float = 0.5          # EMA alpha for vx/vy (0..1)


@dataclass
class PredictConfig:
    lead_time_s: float = 0.45                # servo travel + water time-of-flight
    fps: float = 20.0                        # px/frame <-> px/s; refine on-Pi


@dataclass
class StrategyConfig:
    w_killzone: float = 1.0                  # proximity to kill-zone center
    w_size: float = 0.5                      # bigger/closer target
    w_dwell: float = 0.3                     # how long it has persisted
    w_approach: float = 0.7                  # moving toward the kill-zone
    w_confidence: float = 0.4
    dwell_norm_frames: int = 20              # hits at which dwell score saturates
    switch_hysteresis: float = 0.15          # margin before switching targets
    min_target_dwell_frames: int = 5         # hold a target at least this long


@dataclass
class KillZoneConfig:
    shape: str = "rect"                      # rect | circle
    cx_px: float = 576.0
    cy_px: float = 576.0
    half_w_px: float = 120.0
    half_h_px: float = 120.0
    radius_px: float = 120.0                 # used when shape == circle


@dataclass
class AimConfig:
    # Pixel->angle is an affine map per axis: deg = a*cx + b*cy + c.
    # Defaults reproduce the v1 hand-tuned preset exactly (see fit_calibration to
    # replace with a measured transform on the Pi rig).
    pan_coeffs: List[float] = field(default_factory=lambda: [-0.04, 0.0, 59.04])
    tilt_coeffs: List[float] = field(default_factory=lambda: [0.0, 1.0 / 15.0, -10.4])
    parallax_pan_deg: float = 0.0            # camera-vs-nozzle horizontal offset
    drop_tilt_deg: float = 0.0               # aim-above for water-jet drop (range-dependent)


@dataclass
class ControllerConfig:
    kp: float = 0.02                         # deg per pixel of error
    ki: float = 0.0
    deadband_px: float = 8.0
    max_step_deg: float = 6.0                # per-tick slew cap (anti current-spike)
    integral_limit_deg: float = 5.0          # anti-windup clamp
    backlash_takeup_deg: float = 1.0         # one-directional final-approach overshoot


@dataclass
class ServoConfig:
    i2c_bus: int = 1
    i2c_address: int = 0x40
    pwm_freq_hz: int = 50
    pan_channel: int = 1
    tilt_channel: int = 0
    pan_min_deg: float = 5.0
    pan_max_deg: float = 47.0
    tilt_min_deg: float = 5.0
    tilt_max_deg: float = 25.0
    # v1 angle->pulse: pulse_us = deg * (2000/180) + 501
    pulse_slope_us_per_deg: float = 2000.0 / 180.0
    pulse_offset_us: float = 501.0
    # Absolute pulse safety guard (v1's own internal limit was >500 and <2500us).
    # NOTE: the "1000-2000us MG996R band" cited in the docs does NOT match v1's
    # actual operating pulses (~556-1023us) because of the offset/slope above; we
    # keep v1's verified mapping and a wide hard guard. Re-measure on the Pi.
    pulse_min_us: float = 500.0
    pulse_max_us: float = 2500.0
    # "Center"/boot home. Defaults to geometric mid-travel; replace with the real
    # forward-level pose via the calibration UI (Set Home) and persist it.
    home_pan_deg: float = 26.0
    home_tilt_deg: float = 15.0


@dataclass
class PumpConfig:
    pump_gpio_bcm: int = 26                  # v1 "main laser" pin -> water pump (via relay/MOSFET)
    aux_gpio_bcm: int = 24                    # aux laser/marker — rewired to its own pin (was v1's BCM27)
    status_led_gpio_bcm: int = 23
    active_high: bool = True


@dataclass
class FireConfig:
    enabled: bool = False                    # SAFE default: "would-fire" telemetry only
    fire_duration_s: float = 1.0
    cooldown_s: float = 2.0
    aim_deadband_px: float = 12.0            # max aim error to allow a shot
    require_killzone: bool = True            # predicted position must be in the kill-zone


@dataclass
class AppConfig:
    annotation_mode: str = "off"             # off | fire_frames_only | full_video
    snapshot_mode: str = "off"               # off | every | fire_only | sampled
    snapshot_sample_every: int = 30
    snapshot_dir: str = "dataset"
    detection_mode: str = "full_frame"       # full_frame | motion_gated (seam for 1.10)
    stream_source: str = "usb"               # usb (default) | picam_annotated (debug)
    web_port: int = 8001
    log_level: str = "INFO"
    # Detection-cam debug video: serves the raw lores frame the detector sees as a
    # low-rate JPEG (the web canvas draws boxes/aim/HUD on top). OFF by default —
    # JPEG encode competes with detection; gate it on while debugging only.
    detection_video_enabled: bool = False
    detection_video_max_fps: float = 5.0     # server-side encode-rate cap
    detection_video_quality: int = 70        # JPEG quality (1-100)
    # 1602A LCD on I2C bus 1 (rpi_lcd default addr, alongside the PCA9685 @ 0x40).
    lcd_enabled: bool = True
    lcd_refresh_hz: float = 4.0
    status_led_enabled: bool = True          # BCM23 (v1 status LED): on while ARMED/scanning
    aux_marker_enabled: bool = False         # BCM24 aux laser/marker (rewired from v1's BCM27): OPT-IN, off by default


@dataclass
class RemoteConfig:
    """IR remote control (Step 1.15 — additive hardware on a FREE pin).

    Receiver: bare VS1838B on **BCM25 / gpio_pin=25** (owner-wired) via
    ``dtoverlay=gpio-ir,gpio_pin=25`` in /boot/config.txt → rc-core exposes the remote
    as ``/dev/input/eventN``; ``ir-keytable`` decodes NEC scancodes to the ``KEY_*``
    names below.

    Two consumers share this section:
      * the **supervisor daemon** (``remote_daemon.py`` / ``app/remote_supervisor.py``,
        a separate always-on ``turret-remote.service``) — owns the IR device, runs
        ``systemctl start/stop`` on the turret unit for POWER, and forwards every other
        key to the running app's web API on :8001. This is the active path.
      * the dormant **in-process** ``RemoteListener`` (``app/remote.py``) — kept as a
        seam; reuses ``key_*`` + ``build_key_map``. No-op while ``enabled`` is False.

    Ships ``enabled=False`` (safe default); set ``remote.enabled: true`` in the Pi's
    ``config.local.yaml`` to activate the supervisor. ``KEY_*`` defaults follow the
    21-key NEC remote button map — VERIFY scancodes per unit with ``ir-keytable -t``.
    """
    enabled: bool = False
    gpio_bcm: int = 25                       # owner-wired signal pin (dtoverlay gpio_pin=25)
    # --- IR device resolution (supervisor) ---
    device_name: str = "gpio_ir_recv"        # match evdev device by NAME (eventN index drifts)
    input_device: str = ""                   # optional explicit /dev/input/by-path/... (overrides name)
    grab: bool = True                        # EVIOCGRAB so digits don't leak to a tty (headless)
    oneshot_ignore_autorepeat: bool = True   # one-shots fire on key-down only; jog uses autorepeat
    # --- key -> action map (KEY_* evdev names; verify per unit) ---
    key_estop: str = "KEY_STOP"              # CH-  -> ESTOP (pump off + disarm)
    key_toggle_arm: str = "KEY_CHANNELUP"    # CH+  -> arm/disarm toggle
    key_enable_fire: str = "KEY_MODE"        # EQ   -> toggle fire-enable
    key_center: str = "KEY_HOMEPAGE"         # CH   -> HOME / center
    key_fire: str = "KEY_PLAYPAUSE"          # >||  -> manual FIRE
    key_power: str = "KEY_NUMERIC_0"         # 0    -> POWER toggle: systemctl start/stop turret.service
    key_pan_left: str = "KEY_PREVIOUS"       # |<<  -> jog pan -
    key_pan_right: str = "KEY_NEXT"          # >>|  -> jog pan +
    key_tilt_up: str = "KEY_VOLUMEUP"        # +    -> jog tilt up
    key_tilt_down: str = "KEY_VOLUMEDOWN"    # -    -> jog tilt down
    # --- supervisor forwarding + process control ---
    forward_host: str = "127.0.0.1"          # the running app's web API host
    forward_port: int = 8001                 # app.web_port — where intents are POSTed
    forward_timeout_s: float = 1.0           # per-request HTTP timeout (best-effort)
    turret_unit: str = "turret.service"      # systemd unit the POWER key starts/stops
    repeat_delay_ms: int = 150               # ir-keytable -D (fast slew onset for hold-to-jog)
    repeat_period_ms: int = 110              # ir-keytable -P (near the ~108 ms NEC repeat floor)


@dataclass
class StreamConfig:
    """USB-webcam live stream via a separate mjpg-streamer process (Step 1.12).

    The human-viewable stream is encoded by an external process so the Pi spends
    **no detection compute** on rendering (the Pi-Cam detection path is never
    streamed). Defaults point ``plugin_dir`` at v1's experimental build (the
    rollback) where ``input_uvc.so`` + ``output_http.so`` live; the ``mjpg_streamer``
    binary is expected on PATH or as an absolute ``binary``. Flags are Pi-truth —
    verify the device/resolution/fps the actual webcam supports on-device.
    """
    enabled: bool = True
    device: str = "/dev/video0"             # USB webcam (NOT the Pi Cam)
    width_px: int = 640
    height_px: int = 480
    fps: int = 15
    port: int = 8080                        # http stream port (separate from web_port)
    binary: str = "mjpg_streamer"           # on PATH, or an absolute path
    plugin_dir: str = "v1/mjpg-streamer/mjpg-streamer-experimental"  # .so search dir (rollback reuse)
    input_plugin: str = "input_uvc.so"      # UVC hardware-MJPEG passthrough (low CPU)
    output_plugin: str = "output_http.so"
    www_dir: str = ""                        # optional -w web root (empty -> stream only)


_SECTIONS = {
    "camera": CameraConfig,
    "stream": StreamConfig,
    "detector": DetectorConfig,
    "tracker": TrackerConfig,
    "predict": PredictConfig,
    "strategy": StrategyConfig,
    "killzone": KillZoneConfig,
    "aim": AimConfig,
    "controller": ControllerConfig,
    "servo": ServoConfig,
    "pump": PumpConfig,
    "fire": FireConfig,
    "app": AppConfig,
    "remote": RemoteConfig,
}


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    predict: PredictConfig = field(default_factory=PredictConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    killzone: KillZoneConfig = field(default_factory=KillZoneConfig)
    aim: AimConfig = field(default_factory=AimConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    servo: ServoConfig = field(default_factory=ServoConfig)
    pump: PumpConfig = field(default_factory=PumpConfig)
    fire: FireConfig = field(default_factory=FireConfig)
    app: AppConfig = field(default_factory=AppConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Config":
        data = data or {}
        kwargs: Dict[str, Any] = {}
        for name, section_cls in _SECTIONS.items():
            section_data = data.get(name, {}) or {}
            if not isinstance(section_data, dict):
                raise ConfigError(f"config section '{name}' must be a mapping")
            kwargs[name] = _build_section(section_cls, name, section_data)
        cfg = cls(**kwargs)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        s = self.servo
        if s.pan_min_deg >= s.pan_max_deg or s.tilt_min_deg >= s.tilt_max_deg:
            raise ConfigError("servo clamp min must be < max")
        if s.pulse_min_us >= s.pulse_max_us:
            raise ConfigError("servo pulse_min_us must be < pulse_max_us")
        if self.camera.rotation_deg not in (0, 90, 180, 270):
            raise ConfigError("camera.rotation_deg must be one of 0, 90, 180, 270")
        d = self.detector
        for n in ("conf_threshold", "iou_threshold"):
            v = getattr(d, n)
            if not 0.0 <= v <= 1.0:
                raise ConfigError(f"detector.{n} must be in [0, 1]")
        if d.input_size_px <= 0:
            raise ConfigError("detector.input_size_px must be positive")
        if self.predict.fps <= 0:
            raise ConfigError("predict.fps must be positive")
        if self.fire.fire_duration_s <= 0 or self.fire.cooldown_s < 0:
            raise ConfigError("fire timings invalid")
        if len(self.aim.pan_coeffs) != 3 or len(self.aim.tilt_coeffs) != 3:
            raise ConfigError("aim coeffs must each be length 3 (a, b, c)")
        st = self.stream
        if st.port <= 0:
            raise ConfigError("stream.port must be positive")
        if st.fps <= 0:
            raise ConfigError("stream.fps must be positive")
        if st.width_px <= 0 or st.height_px <= 0:
            raise ConfigError("stream width/height must be positive")


def _build_section(section_cls: Any, name: str, data: Dict[str, Any]) -> Any:
    known = {f.name for f in fields(section_cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown keys in config section '{name}': {sorted(unknown)}")
    return section_cls(**data)


def _load_yaml(path: str) -> Dict[str, Any]:
    """Read a YAML mapping. Missing file / no PyYAML -> {} (Mac tests don't need yaml)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    with open(path, "r") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a top-level mapping")
    return loaded


def load_config(path: str = "config.yaml",
                local_path: str = "config.local.yaml") -> Config:
    """Load config: typed defaults <- ``config.yaml`` (documented base) <- ``config.local.yaml``.

    ``config.local.yaml`` is the machine-written overlay (calibration, home, live
    tuning saved from the web UI); it is git-ignored so each box keeps its own and a
    deploy never clobbers it. Per-key merge, so editing the base still affects keys
    the overlay didn't touch.
    """
    data = _load_yaml(path)
    for section, vals in _load_yaml(local_path).items():
        if isinstance(vals, dict) and isinstance(data.get(section), dict):
            data[section].update(vals)
        else:
            data[section] = vals
    return Config.from_dict(data)


def save_local_config(cfg: Config, local_path: str = "config.local.yaml",
                      base_path: str = "config.yaml") -> Dict[str, Any]:
    """Persist only what differs from the base to ``config.local.yaml`` (atomic).

    Writing the delta (not the whole config) keeps the overlay small and lets later
    base edits still apply to untouched keys. Returns the written delta.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ConfigError("PyYAML is required to save config (pip install PyYAML)") from exc
    cfg.validate()
    base = Config.from_dict(_load_yaml(base_path)).to_dict()
    delta: Dict[str, Any] = {}
    for section, vals in cfg.to_dict().items():
        base_vals = base.get(section, {})
        diff = {k: v for k, v in vals.items() if base_vals.get(k) != v}
        if diff:
            delta[section] = diff
    tmp = local_path + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump(delta, fh, default_flow_style=False, sort_keys=False)
    os.replace(tmp, local_path)
    return delta
