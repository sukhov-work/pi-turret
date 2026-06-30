"""IR remote SUPERVISOR daemon (Step 1.15) — a separate, always-on process.

Why a supervisor (not the in-process ``app/remote.py`` listener): only ONE process can
``EVIOCGRAB`` the IR ``/dev/input/eventN`` device, and only an always-on process can
``systemctl start`` the main app when it is STOPPED. So this daemon owns the IR device
and:

  * **POWER** key  → ``systemctl start|stop`` the turret unit (manage the main app), and
  * every other key → an HTTP POST to the RUNNING app's web control API on :8001
    (``/api/cmd`` / ``/api/control-cmd`` — exactly what the web UI already calls).

It **never** touches PCA9685 / pump / servos: the app's control thread stays the single
servo mover. Everything is best-effort — a remote/app/systemctl fault is logged and
swallowed so the listener never dies. ``evdev`` is imported lazily so this module imports
(and its pure helpers unit-test) on the Mac.

Runs as ``turret-remote.service`` (root: reads /dev/input AND runs systemctl). Feature-gated
on ``remote.enabled``; when False the daemon logs and exits 0.

Python 3.9 on-device: no ``match``, no ``X | Y`` unions.
"""
from __future__ import annotations

import json
import logging
import select
import signal
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request

from config import RemoteConfig

logger = logging.getLogger("remote")

# Intents driven by holding a key (kernel autorepeat = value 2): each event = one jog step.
JOG_INTENTS = frozenset(
    {"JOG_PAN_NEG", "JOG_PAN_POS", "JOG_TILT_POS", "JOG_TILT_NEG"}
)

# Map a jog intent to the web UI's /api/control-cmd direction codes (app/web.py::_JOG).
_JOG_CODE = {
    "JOG_PAN_NEG": "left",
    "JOG_PAN_POS": "right",
    "JOG_TILT_POS": "up",
    "JOG_TILT_NEG": "down",
}


def build_intent_map(cfg: RemoteConfig) -> Dict[str, str]:
    """KEY_* evdev name -> intent string. Pure (Mac unit-tested).

    Unset (empty) key bindings are dropped so they never shadow a real key.
    """
    mapping = {
        cfg.key_estop: "ESTOP",
        cfg.key_toggle_arm: "ARM_TOGGLE",
        cfg.key_enable_fire: "TOGGLE_FIRE_ENABLE",
        cfg.key_center: "HOME",
        cfg.key_fire: "FIRE",
        cfg.key_power: "POWER_TOGGLE",
        cfg.key_pan_left: "JOG_PAN_NEG",
        cfg.key_pan_right: "JOG_PAN_POS",
        cfg.key_tilt_up: "JOG_TILT_POS",
        cfg.key_tilt_down: "JOG_TILT_NEG",
    }
    mapping.pop("", None)
    return mapping


def http_plan(intent: str) -> List[Tuple[str, str]]:
    """Intent -> ordered list of (web_path, raw_body) POSTs. Pure (Mac unit-tested).

    Reuses the app's existing web command vocabulary (app/web.py::command /
    manual_control). ESTOP is pump-off THEN disarm so the jet de-energizes first.
    ARM_TOGGLE / POWER_* are handled imperatively (they need a state read) and are
    intentionally absent here.
    """
    if intent == "ESTOP":
        return [("/api/cmd", "pump_off"), ("/api/cmd", "disarm")]
    if intent == "TOGGLE_FIRE_ENABLE":
        return [("/api/cmd", "toggle_fire")]
    if intent == "HOME":
        return [("/api/cmd", "center")]
    if intent == "FIRE":
        return [("/api/cmd", "fire_now")]
    if intent in _JOG_CODE:
        return [("/api/control-cmd", _JOG_CODE[intent])]
    return []


class IntentForwarder:
    """Turns intents into web-API POSTs / systemctl calls. No hardware access.

    Split out from the listener so it can be unit-tested with the pure planners and so
    the evdev thread only ever calls :meth:`dispatch`.
    """

    def __init__(self, cfg: RemoteConfig):
        self.cfg = cfg
        self.base_url = "http://%s:%d" % (cfg.forward_host, cfg.forward_port)

    def dispatch(self, intent: str) -> None:
        if intent == "ARM_TOGGLE":
            state = self._turret_state()  # "Enabled" / "Disabled" / None
            self._post("/api/cmd", "disarm" if state == "Enabled" else "arm")
            return
        if intent == "POWER_TOGGLE":
            self._systemctl("stop" if self._unit_active() else "start")
            return
        if intent == "POWER_ON":
            self._systemctl("start")
            return
        if intent == "POWER_OFF":
            self._systemctl("stop")
            return
        steps = http_plan(intent)
        if not steps:
            logger.debug("no handler for intent %s", intent)
            return
        for path, body in steps:
            self._post(path, body)

    # ---- web API (the app's existing control endpoints) ----

    def _post(self, path: str, body: str) -> None:
        url = self.base_url + path
        req = request.Request(
            url, data=(body or "").encode("utf-8"), method="POST",
            headers={"Content-Type": "text/plain"},
        )
        try:
            with request.urlopen(req, timeout=self.cfg.forward_timeout_s) as resp:
                logger.debug("POST %s %r -> %s", path, body, resp.status)
        except urlerror.HTTPError as exc:  # app reachable but refused (e.g. jog while armed)
            logger.info("POST %s %r refused: %s (app running but rejected)", path, body, exc.code)
        except Exception:  # noqa: BLE001 — app down / no socket: best-effort, never crash
            logger.warning("POST %s %r failed (app not running?)", path, body)

    def _turret_state(self) -> Optional[str]:
        try:
            with request.urlopen(self.base_url + "/api/turret-state",
                                 timeout=self.cfg.forward_timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8")).get("state")
        except Exception:  # noqa: BLE001 — app down: ARM_TOGGLE falls back to "arm"
            return None

    # ---- process control (manage the main app) ----

    def _systemctl(self, action: str) -> None:
        unit = self.cfg.turret_unit
        try:
            subprocess.run(["systemctl", action, unit], check=False, timeout=15)
            logger.info("systemctl %s %s", action, unit)
        except Exception:  # noqa: BLE001 — never crash the listener on a systemctl fault
            logger.warning("systemctl %s %s failed", action, unit, exc_info=True)

    def _unit_active(self) -> bool:
        try:
            res = subprocess.run(
                ["systemctl", "is-active", self.cfg.turret_unit],
                capture_output=True, text=True, timeout=15,
            )
            return res.stdout.strip() == "active"
        except Exception:  # noqa: BLE001
            return False


class RemoteSupervisor:
    """Blocking evdev read loop: maps KEY_* events to intents and dispatches them.

    Runs in the daemon's main thread (no servo work here, so blocking reads are fine).
    Reconnects if the device renumbers/replugs. Best-effort throughout.
    """

    def __init__(self, cfg: RemoteConfig, forwarder: Optional[IntentForwarder] = None):
        self.cfg = cfg
        self.intents = build_intent_map(cfg)
        self.forwarder = forwarder if forwarder is not None else IntentForwarder(cfg)
        self._stop = threading.Event()
        self._dev = None

    def run(self) -> None:
        from evdev import ecodes  # lazy: Pi-only

        logger.info("IR supervisor up: device=%r url=%s unit=%s grab=%s; map=%s",
                    self.cfg.device_name, self.forwarder.base_url, self.cfg.turret_unit,
                    self.cfg.grab, self.intents)
        while not self._stop.is_set():
            try:
                if self._dev is None:
                    self._dev = self._open_device()
                    if self._dev is None:
                        time.sleep(1.0)
                        continue
                ready, _, _ = select.select([self._dev.fd], [], [], 0.5)
                if not ready:
                    continue
                for ev in self._dev.read():
                    if ev.type == ecodes.EV_KEY:
                        self._handle(ev, ecodes)
            except OSError:  # device vanished (replug / eventN renumber)
                logger.warning("IR device lost; will reopen")
                self._close()
                time.sleep(0.5)
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.warning("supervisor loop error", exc_info=True)
                time.sleep(0.2)
        self._close()

    def stop(self) -> None:
        self._stop.set()

    def _open_device(self):
        import evdev  # lazy: Pi-only

        dev = None
        if self.cfg.input_device:
            try:
                dev = evdev.InputDevice(self.cfg.input_device)
            except Exception:  # noqa: BLE001 — fall back to name resolution
                dev = None
        if dev is None:
            for path in evdev.list_devices():
                cand = evdev.InputDevice(path)
                if cand.name == self.cfg.device_name:
                    dev = cand
                    break
                cand.close()
        if dev is None:
            return None
        if self.cfg.grab:
            try:
                dev.grab()
            except Exception:  # noqa: BLE001 — grab is a nicety, not a requirement
                logger.warning("EVIOCGRAB failed on %s", dev.path, exc_info=True)
        logger.info("IR device opened: %s (%s)", dev.path, dev.name)
        return dev

    def _close(self) -> None:
        if self._dev is None:
            return
        try:
            if self.cfg.grab:
                self._dev.ungrab()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._dev.close()
        except Exception:  # noqa: BLE001
            pass
        self._dev = None

    def _handle(self, ev, ecodes) -> None:
        name = ecodes.KEY.get(ev.code)
        if isinstance(name, list):  # some codes alias to multiple KEY_* names
            name = name[0]
        intent = self.intents.get(name)
        if intent is None:
            return
        if ev.value == 0:  # key up
            return
        is_fresh = ev.value == 1  # 1 = key down, 2 = autorepeat
        if intent not in JOG_INTENTS and self.cfg.oneshot_ignore_autorepeat and not is_fresh:
            return  # one-shot: act on the initial press only
        logger.info("IR %s -> %s%s", name, intent, "" if is_fresh else " (held)")
        try:
            self.forwarder.dispatch(intent)
        except Exception:  # noqa: BLE001 — a dispatch fault must never crash control
            logger.warning("dispatch failed for %s", intent, exc_info=True)


def main() -> None:
    from config import load_config

    cfg_all = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg_all.app.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = cfg_all.remote
    if not cfg.enabled:
        logger.warning("remote.enabled is False — IR supervisor idle. "
                       "Set remote.enabled: true in config.local.yaml to activate.")
        return

    supervisor = RemoteSupervisor(cfg)

    def _on_signal(signum, _frame):
        logger.info("signal %s -> stopping IR supervisor", signum)
        supervisor.stop()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    supervisor.run()


if __name__ == "__main__":
    main()
