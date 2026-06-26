"""pi-turret v2 entrypoint — Pi-only run; imports clean on the Mac.

Builds the hardware singletons (PCA9685 servo bus, pump relay, Pi Camera, 1602A
LCD, status/aux indicators), wires the threaded pipeline, optionally starts the IR
remote, and registers disarm handlers so any exit converges to the safe state
(servos centered + relaxed, pump off, status LED off, LCD shows SAFE).

Reuses v1's exact wiring (BCM26 pump, BCM27 aux, BCM23 status LED, I2C bus 1 for
PCA9685 @ 0x40 + the LCD). All hardware construction is lazy/guarded.

Run on the Pi:  python3 main.py
"""
from __future__ import annotations

import atexit
import logging
import signal
import socket

from actuate.indicators import GpioOutput
from actuate.lcd import StatusLcd
from actuate.pca9685 import PCA9685
from actuate.pump import Pump
from actuate.servo import Axis, ServoController
from app.control import ControlLoop
from app.display import LcdReporter
from app.pipeline import Pipeline
from app.remote import RemoteActions, RemoteListener
from app.statemachine import FireState, FireStateMachine
from config import Config, load_config
from detect.coral import CoralDetector
from strategy.selector import TargetSelector
from track.tracker import IouTracker

logger = logging.getLogger(__name__)


def _lan_ip() -> str:
    """Best-effort LAN IP (v1's trick: a UDP socket to a public addr, never sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "0.0.0.0"
    finally:
        s.close()


class TurretRemoteActions(RemoteActions):
    """Maps IR remote keys to live turret actions (arm/fire/center/jog)."""

    def __init__(self, cfg: Config, servo: ServoController, sm: FireStateMachine,
                 jog_step_deg: float = 2.0):
        self._cfg = cfg
        self._servo = servo
        self._sm = sm
        self._step = jog_step_deg

    def toggle_arm(self) -> None:
        self._sm.reset() if self._sm.state is FireState.SAFE else self._sm.enter_safe()

    def toggle_fire_enabled(self) -> None:
        self._cfg.fire.enabled = not self._cfg.fire.enabled

    def center(self) -> None:
        self._servo.center()

    def jog(self, axis: str, direction: int) -> None:
        ax = Axis.PAN if axis == "pan" else Axis.TILT
        self._servo.set_angle(ax, self._servo.last_angle(ax) + direction * self._step)


def build_pipeline(cfg: Config):
    """Construct hardware + threads. Pi-only (touches I2C / GPIO / camera)."""
    from capture import PiCamCapture

    driver = PCA9685(address=cfg.servo.i2c_address, busnum=cfg.servo.i2c_bus)
    driver.setup(cfg.servo.pwm_freq_hz)
    servo = ServoController(driver, cfg.servo)
    servo.center()

    pump = Pump(gpio_bcm=cfg.pump.pump_gpio_bcm, active_high=cfg.pump.active_high)
    status_led = GpioOutput(cfg.pump.status_led_gpio_bcm, enabled=cfg.app.status_led_enabled)
    aux_marker = GpioOutput(cfg.pump.aux_gpio_bcm, enabled=cfg.app.aux_marker_enabled)

    sm = FireStateMachine(cfg.fire, on_fire=lambda: pump.fire(cfg.fire.fire_duration_s),
                          off_fire=pump.off)
    selector = TargetSelector(cfg.strategy.switch_hysteresis,
                              cfg.strategy.min_target_dwell_frames)
    control = ControlLoop(cfg, servo, selector, sm,
                          status_led=status_led, aux_marker=aux_marker)

    detector = CoralDetector(cfg.detector)
    capture = PiCamCapture(cfg.camera, cfg.detector.input_size_px)
    capture.start()
    tracker = IouTracker(cfg.tracker.iou_match_threshold, cfg.tracker.max_age_frames,
                         cfg.tracker.min_hits, cfg.tracker.velocity_smoothing)

    lcd = StatusLcd(enabled=cfg.app.lcd_enabled)
    pipeline = Pipeline(capture, detector, control, tracker)
    reporter = LcdReporter(lcd, pipeline.latest_telemetry, cfg.app.lcd_refresh_hz,
                           armed_getter=lambda: cfg.fire.enabled,
                           fps_getter=lambda: pipeline.fps,
                           shots_getter=lambda: pipeline.shots)
    pipeline.reporter = reporter

    remote = RemoteListener(cfg.remote, TurretRemoteActions(cfg, servo, sm))

    def disarm() -> None:
        logger.info("disarming -> safe state")
        try:
            sm.enter_safe()
            pump.close()
            status_led.off()
            aux_marker.off()
            reporter.stop()
            lcd.show("** SAFE **", "disarmed")
        finally:
            servo.disarm()

    return pipeline, reporter, remote, disarm


def main() -> None:
    cfg = load_config()
    logging.basicConfig(level=getattr(logging, cfg.app.log_level, logging.INFO))
    pipeline, reporter, remote, disarm = build_pipeline(cfg)

    def _handle_signal(signum, _frame):
        disarm()
        raise SystemExit(0)

    atexit.register(disarm)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    reporter.message("pi-turret v2", f"{_lan_ip()}:{cfg.app.web_port}")
    logger.info("starting pipeline (fire.enabled=%s)", cfg.fire.enabled)
    pipeline.start()
    remote.start()
    signal.pause()  # threads are daemons; block the main thread until a signal


if __name__ == "__main__":
    main()
