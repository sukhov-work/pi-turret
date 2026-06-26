"""IR remote control seam (PROPOSED — Pi-only; needs captured key codes).

v1 has **no GPIO inputs**, so this is purely additive: one IR receiver on a FREE
pin (proposed BCM17) plus ``dtoverlay=gpio-ir,gpio_pin=17`` in /boot/config.txt,
which exposes the remote as an evdev device (rc-core). Capture the actual key
names once with ``ir-keytable -t`` and put them in ``RemoteConfig``.

This module is a documented stub: ``RemoteActions`` is the interface the turret
implements (arm/disarm, enable fire, center, jog); ``RemoteListener`` reads evdev
key-down events and dispatches them. evdev is imported lazily so the module imports
on the Mac. Not wired to run unless ``remote.enabled`` and on the Pi.

Approach options (decide on the Pi):
  - rc-core + evdev via ``dtoverlay=gpio-ir`` (recommended on Bullseye) — used here.
  - LIRC (older, heavier) or pigpio software decode (no overlay) — alternatives.
"""
from __future__ import annotations

import abc
import logging
import threading
from typing import Dict, Optional

from config import RemoteConfig

logger = logging.getLogger(__name__)


class RemoteActions(abc.ABC):
    """What an IR remote can do. Implemented by the app over servo + state machine."""

    @abc.abstractmethod
    def toggle_arm(self) -> None: ...

    @abc.abstractmethod
    def toggle_fire_enabled(self) -> None: ...

    @abc.abstractmethod
    def center(self) -> None: ...

    @abc.abstractmethod
    def jog(self, axis: str, direction: int) -> None: ...


def build_key_map(cfg: RemoteConfig, actions: RemoteActions) -> Dict[str, object]:
    """Map evdev KEY_* names -> zero-arg callables. Pure (testable)."""
    return {
        cfg.key_toggle_arm: actions.toggle_arm,
        cfg.key_enable_fire: actions.toggle_fire_enabled,
        cfg.key_center: actions.center,
        cfg.key_pan_left: lambda: actions.jog("pan", -1),
        cfg.key_pan_right: lambda: actions.jog("pan", +1),
        cfg.key_tilt_up: lambda: actions.jog("tilt", +1),
        cfg.key_tilt_down: lambda: actions.jog("tilt", -1),
    }


class RemoteListener:
    """Reads evdev key-down events and dispatches mapped actions (Pi-only run)."""

    def __init__(self, cfg: RemoteConfig, actions: RemoteActions):
        self.cfg = cfg
        self._key_map = build_key_map(cfg, actions)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.cfg.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="remote", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        try:
            import evdev  # lazy: Pi-only
            dev = evdev.InputDevice(self.cfg.input_device)
            for event in dev.read_loop():
                if not self._running:
                    break
                if event.type == evdev.ecodes.EV_KEY and event.value == 1:  # key down
                    key = evdev.ecodes.KEY.get(event.code)
                    action = self._key_map.get(key)
                    if action is not None:
                        logger.info("remote key %s", key)
                        action()
        except Exception:  # noqa: BLE001 — remote is best-effort, never crash control
            logger.warning("remote listener stopped", exc_info=True)
