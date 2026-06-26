"""Exception hierarchy for pi-turret v2.

On any error in an actuation path, callers fail to a safe state (pump off, servos
relaxed). Pure-logic layers let bugs raise so tests catch them.
"""


class TurretError(Exception):
    """Base for all pi-turret errors."""


class ConfigError(TurretError):
    """Invalid or missing tunable / calibration."""


class CameraError(TurretError):
    """picamera2 init or capture failed."""


class DetectionError(TurretError):
    """Model load or inference failed."""


class ServoError(TurretError):
    """PCA9685 / I2C write failed — safety-critical."""


class PumpError(TurretError):
    """Relay / pump actuation failed — safety-critical."""
