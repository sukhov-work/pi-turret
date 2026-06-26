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


@dataclass
class DetectorConfig:
    backend: str = "coral_yolo"              # coral_yolo | coral_mobiledet | cpu
    model_path: str = "models/bird_yolov8n_256_int8_edgetpu.tflite"
    input_size_px: int = 256                 # YOLOv8n@256 primary; MobileDet uses 320
    num_classes: int = 1                     # single-class "bird"
    conf_threshold: float = 0.25
    iou_threshold: float = 0.5
    coords_normalized: bool = False          # pinned by the golden fixture on Strix/Pi


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
    home_pan_deg: float = 31.0
    home_tilt_deg: float = 23.0


@dataclass
class PumpConfig:
    pump_gpio_bcm: int = 26                  # v1 "main laser" pin -> water pump (via relay/MOSFET)
    aux_gpio_bcm: int = 27
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
    # 1602A LCD on I2C bus 1 (rpi_lcd default addr, alongside the PCA9685 @ 0x40).
    lcd_enabled: bool = True
    lcd_refresh_hz: float = 4.0
    status_led_enabled: bool = True          # BCM23 (v1 status LED): on while ARMED/scanning
    aux_marker_enabled: bool = False         # BCM27 (v1 aux laser): OPT-IN aim marker, off by default


@dataclass
class RemoteConfig:
    """IR remote control (PROPOSED — additive hardware, see IMPLEMENTATION_PLAN 1.15).

    v1 has no GPIO inputs, so the IR receiver is a NEW wire on a FREE pin plus a
    ``dtoverlay=gpio-ir`` in /boot/config.txt. ``gpio_bcm`` is a proposal — confirm
    before wiring. With rc-core/evdev the key names come from ``ir-keytable``.
    """
    enabled: bool = False
    gpio_bcm: int = 17                       # PROPOSED free pin (confirm before wiring)
    input_device: str = ""                   # evdev path, e.g. /dev/input/eventN
    key_toggle_arm: str = "KEY_POWER"
    key_enable_fire: str = "KEY_OK"
    key_center: str = "KEY_HOME"
    key_pan_left: str = "KEY_LEFT"
    key_pan_right: str = "KEY_RIGHT"
    key_tilt_up: str = "KEY_UP"
    key_tilt_down: str = "KEY_DOWN"


_SECTIONS = {
    "camera": CameraConfig,
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


def _build_section(section_cls: Any, name: str, data: Dict[str, Any]) -> Any:
    known = {f.name for f in fields(section_cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown keys in config section '{name}': {sorted(unknown)}")
    return section_cls(**data)


def load_config(path: str = "config.yaml") -> Config:
    """Load config from a YAML file layered over defaults.

    Missing file -> defaults. PyYAML missing -> defaults (with the file ignored),
    so pure-logic Mac tests never hard-require the yaml dependency.
    """
    data: Dict[str, Any] = {}
    if path and os.path.exists(path):
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None  # type: ignore
        if yaml is not None:
            with open(path, "r") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ConfigError(f"{path} must contain a top-level mapping")
            data = loaded
    return Config.from_dict(data)
