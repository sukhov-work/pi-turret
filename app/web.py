"""Bottle web UI for pi-turret v2 (Step 1.11).

Extends v1's Bottle approach (it does **not** touch v1): same route *names*
(``/api/cmd``, ``/api/control-cmd``, ``/api/turret-state``) for muscle memory,
plus ``/api/telemetry`` and ``/api/config`` for live monitoring and tuning.

Split in two:
  * ``TurretWebController`` — pure logic (no Bottle), unit-tested on the Mac. It
    reads telemetry off the pipeline's lock-protected latest-slots and mutates the
    shared ``Config`` by **atomic whole-section swaps**, so the control thread
    never reads a half-updated section. After a swap it calls
    ``ControlLoop.apply_config()`` to refresh the few values snapshotted at init.
  * A thin Bottle adapter (``create_app`` / ``serve``) with a **lazy** ``import
    bottle`` so the Mac test venv (no bottle) still imports this module.

The web server runs on its own daemon thread; it only swaps config references and
reads latest-slots, so it can never stall the control loop. Manual jog is refused
unless the turret is disarmed (SAFE) — the control thread is the only servo mover
while armed (mirrors v1's guard).
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import asdict, fields
from typing import Any, Dict, Optional

from actuate.servo import Axis
from config import _SECTIONS
from errors import ConfigError

logger = logging.getLogger(__name__)

# Every config section is tunable + persistable from the UI. Changes are validated
# (Config.validate, with rollback) and re-synced into the live objects where that is
# safe (see _resync); fields that need a restart to take effect (model_path, capture
# dims, i2c/pins, ports) still persist and apply on next boot — flagged in the UI docs.
EDITABLE_SECTIONS = tuple(_SECTIONS)

# Jog directions are physical: UP must raise the aim. On this rig a HIGHER tilt
# angle points the barrel DOWN, so "up" decreases tilt. Pan keeps +deg = left.
_JOG = {
    "up": (Axis.TILT, -1),
    "down": (Axis.TILT, +1),
    "left": (Axis.PAN, +1),
    "right": (Axis.PAN, -1),
    "stop": (None, 0),
}


def _finite(x: Any) -> Any:
    """JSON-safe number: map inf/nan -> None so JSON.parse never chokes."""
    if isinstance(x, (int, float)):
        return x if math.isfinite(x) else None
    return x


def _coerce(old_value: Any, new_value: Any) -> Any:
    """Coerce a JSON/form value to the type of the existing field (HTML sends strings)."""
    if isinstance(old_value, bool):
        if isinstance(new_value, str):
            return new_value.strip().lower() in ("1", "true", "yes", "on")
        return bool(new_value)
    if isinstance(old_value, int):  # bool already handled above
        return int(new_value)
    if isinstance(old_value, float):
        return float(new_value)
    if isinstance(old_value, list):
        return [float(v) for v in new_value]
    return new_value


class TurretWebController:
    """Framework-free brains behind the web routes (Mac-testable)."""

    def __init__(self, cfg, pipeline, control, streamer=None, jog_step_deg: float = 2.0):
        self.cfg = cfg
        self.pipeline = pipeline
        self.control = control
        self.streamer = streamer            # optional UsbStreamer (Pi-side), may be None
        self.jog_step_deg = jog_step_deg
        self._jpeg_cache = None             # (monotonic_t, bytes) for the detection view
        self._cal_samples = []              # [(cx, cy, pan_deg, tilt_deg)] calibration points


    # ---- read ----

    def telemetry(self) -> Dict[str, Any]:
        from app.statemachine import FireState

        t = self.pipeline.latest_telemetry.get()
        tracks = self.pipeline.latest_tracks.get() or []
        out: Dict[str, Any] = {
            "fps": round(float(self.pipeline.fps), 2),
            "shots": int(self.pipeline.shots),
            "fire_enabled": bool(self.cfg.fire.enabled),
            "armed": self.control.sm.state is not FireState.SAFE,
            "tracks": [
                {
                    "id": tr.id, "cx": round(tr.cx, 1), "cy": round(tr.cy, 1),
                    "score": round(tr.score, 3), "vx": round(tr.vx, 2),
                    "vy": round(tr.vy, 2), "hits": tr.hits,
                }
                for tr in tracks
            ],
        }
        if t is not None:
            px = t.predicted_xy
            out.update({
                "state": t.state.value,
                "num_tracks": t.num_tracks,
                "selected_target_id": t.selected_target_id,
                "aim_error_px": _finite(t.aim_error_px),
                "predicted_xy": [_finite(px[0]), _finite(px[1])] if px else None,
                "pan_cmd_deg": _finite(t.pan_cmd_deg),
                "tilt_cmd_deg": _finite(t.tilt_cmd_deg),
                "in_killzone": t.in_killzone,
                "would_fire": t.would_fire,
            })
        else:
            out["state"] = self.control.sm.state.value
        out["stream"] = {
            "source": self.cfg.app.stream_source,
            "running": self.streamer.is_running() if self.streamer is not None else False,
            "port": self.cfg.stream.port,
        }
        out["detection_video"] = {"enabled": bool(self.cfg.app.detection_video_enabled)}
        # Geometry for the tactical display (detection-frame pixel space).
        out["frame"] = {"w": int(self.cfg.camera.capture_width_px),
                        "h": int(self.cfg.camera.capture_height_px)}
        out["killzone"] = asdict(self.cfg.killzone)
        s = self.cfg.servo
        out["servo_limits"] = {
            "pan_min_deg": s.pan_min_deg, "pan_max_deg": s.pan_max_deg,
            "tilt_min_deg": s.tilt_min_deg, "tilt_max_deg": s.tilt_max_deg,
            "home_pan_deg": s.home_pan_deg, "home_tilt_deg": s.home_tilt_deg,
        }
        out["cal_samples"] = len(self._cal_samples)
        out["camera"] = {"rotation_deg": int(self.cfg.camera.rotation_deg)}
        return out

    def config_snapshot(self) -> Dict[str, Any]:
        return {name: asdict(getattr(self.cfg, name)) for name in EDITABLE_SECTIONS}

    def detection_jpeg(self) -> Optional[bytes]:
        """JPEG of the raw lores frame the detector sees (debug). None when disabled
        / no frame. Rate-capped server-side so a fast client can't starve detection.
        """
        if not self.cfg.app.detection_video_enabled:
            return None
        min_dt = 1.0 / max(0.5, float(self.cfg.app.detection_video_max_fps))
        now = time.monotonic()
        if self._jpeg_cache is not None and (now - self._jpeg_cache[0]) < min_dt:
            return self._jpeg_cache[1]
        frame = self.pipeline.latest_frame.get()
        if frame is None:
            return None
        from app.debugview import encode_jpeg
        data = encode_jpeg(frame, self.cfg.app.detection_video_quality)
        if data is not None:
            self._jpeg_cache = (now, data)
        return data

    def turret_state(self) -> Dict[str, str]:
        """v1-compatible: 'Enabled' when armed (auto-tracking), else 'Disabled'."""
        from app.statemachine import FireState

        armed = self.control.sm.state is not FireState.SAFE
        return {"state": "Enabled" if armed else "Disabled"}

    # ---- write ----

    def command(self, code: Optional[str]) -> Dict[str, Any]:
        code = (code or "").strip()
        if code in ("arm", "enable_turret"):
            self.control.sm.reset()
        elif code in ("disarm", "disable_turret"):
            self.control.sm.enter_safe()
        elif code == "enable_fire":
            self.cfg.fire.enabled = True
        elif code == "disable_fire":
            self.cfg.fire.enabled = False
        elif code == "toggle_fire":
            self.cfg.fire.enabled = not self.cfg.fire.enabled
        elif code == "center":
            self.control.servo.center()
        elif code in ("fire_now", "manual_fire"):
            if not self.control.manual_fire():
                return {"ok": False, "error": "no pump wired"}
            return {"ok": True, "command": code}
        elif code in ("pump_off", "fire_stop"):
            self.control.manual_pump_off()
            return {"ok": True, "command": code}
        elif code in ("enable_aux", "enable_aux_laser"):
            self.cfg.app.aux_marker_enabled = True
            self.control.apply_config()
        elif code in ("disable_aux", "disable_aux_laser"):
            self.cfg.app.aux_marker_enabled = False
            self.control.apply_config()
        elif code in ("marker_on", "aux_on"):
            return {"ok": True, "command": code, "marker_on": self.control.set_marker(True)}
        elif code in ("marker_off", "aux_off"):
            return {"ok": True, "command": code, "marker_on": self.control.set_marker(False)}
        elif code in ("detect_video_on", "detect_video_off"):
            self.cfg.app.detection_video_enabled = (code == "detect_video_on")
            return {"ok": True, "command": code,
                    "detection_video": self.cfg.app.detection_video_enabled}
        elif code == "stream_usb":
            if self.streamer is None:
                return {"ok": False, "error": "no streamer configured"}
            self.cfg.app.stream_source = "usb"
            return {"ok": True, "command": code, "stream_running": self.streamer.start()}
        elif code == "stream_off":
            if self.streamer is None:
                return {"ok": False, "error": "no streamer configured"}
            self.streamer.stop()
            return {"ok": True, "command": code, "stream_running": False}
        else:
            return {"ok": False, "error": f"unknown command: {code!r}"}
        return {"ok": True, "command": code, **self.turret_state()}

    def manual_control(self, code: Optional[str]) -> Dict[str, Any]:
        from app.statemachine import FireState

        code = (code or "").strip()
        if code not in _JOG:
            return {"ok": False, "error": f"unknown control: {code!r}"}
        if self.control.sm.state is not FireState.SAFE:
            return {"ok": False, "error": "manual control only when disarmed"}
        axis, direction = _JOG[code]
        if axis is None:  # stop
            return {"ok": True, "command": code}
        cur = self.control.servo.last_angle(axis)
        new_angle = self.control.servo.set_angle(axis, cur + direction * self.jog_step_deg)
        return {"ok": True, "command": code, "angle_deg": round(new_angle, 2)}

    def update_config(self, section: Optional[str], changes: Dict[str, Any]) -> Dict[str, Any]:
        if section not in EDITABLE_SECTIONS:
            return {"ok": False, "error": f"section not tunable: {section!r}"}
        section_cls = _SECTIONS[section]
        current = getattr(self.cfg, section)
        known = {f.name for f in fields(section_cls)}

        merged = asdict(current)
        try:
            for key, value in (changes or {}).items():
                if key not in known:
                    raise ConfigError(f"unknown key '{section}.{key}'")
                merged[key] = _coerce(merged[key], value)
            new_section = section_cls(**merged)
        except (ConfigError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

        old_section = current
        setattr(self.cfg, section, new_section)  # atomic whole-section swap
        try:
            self.cfg.validate()
        except ConfigError as exc:
            setattr(self.cfg, section, old_section)  # rollback
            return {"ok": False, "error": str(exc)}

        self._resync()  # re-point every live object at the new cfg
        logger.info("config updated: %s <- %s", section, changes)
        return {"ok": True, "section": section, "values": asdict(new_section)}

    def _resync(self) -> None:
        """After a section swap, re-point every live object at the current cfg so
        edits to any section take effect immediately where safe. ``ControlLoop``
        refreshes its own snapshots; the long-lived hardware objects (held by the
        pipeline) each adopt their section. Restart-only fields are simply re-pointed
        (no effect until reboot). Missing objects (tests) are skipped.
        """
        self.control.apply_config()
        pipe = self.pipeline
        self._apply_if(getattr(self.control, "servo", None), self.cfg.servo)
        self._apply_if(getattr(pipe, "tracker", None), self.cfg.tracker)
        self._apply_if(getattr(pipe, "capture", None), self.cfg.camera)
        self._apply_if(getattr(pipe, "detector", None), self.cfg.detector)

    @staticmethod
    def _apply_if(obj, section_cfg) -> None:
        fn = getattr(obj, "apply_config", None)
        if callable(fn):
            fn(section_cfg)

    # ---- calibration + persistence (Phase C) ----

    def calibrate(self, action: Optional[str], payload: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
        """Dispatch a calibration action (Set Home, limits, sample/fit, save)."""
        payload = payload or {}
        action = (action or "").strip()
        if action == "set_home":
            return self._set_home()
        if action == "set_rotation":
            return self._set_rotation(payload)
        if action == "set_limits":
            return self._set_limits(payload)
        if action == "add_sample":
            return self._cal_add(payload)
        if action == "clear":
            self._cal_samples.clear()
            return {"ok": True, "samples": 0}
        if action == "fit":
            return self._cal_fit()
        if action == "save":
            return self.save_config()
        if action == "status":
            return {"ok": True, "samples": len(self._cal_samples)}
        return {"ok": False, "error": f"unknown calibrate action: {action!r}"}

    def _disarmed(self) -> bool:
        from app.statemachine import FireState
        return self.control.sm.state is FireState.SAFE

    def _set_home(self) -> Dict[str, Any]:
        """Record the current servo pose as the boot/Center home (persist with save)."""
        pan = round(self.control.servo.last_angle(Axis.PAN), 2)
        tilt = round(self.control.servo.last_angle(Axis.TILT), 2)
        self.cfg.servo.home_pan_deg = pan      # in-place: ServoController shares this object
        self.cfg.servo.home_tilt_deg = tilt
        logger.info("home set to pan=%.2f tilt=%.2f deg", pan, tilt)
        return {"ok": True, "home": [pan, tilt]}

    def _set_rotation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Set the camera rotation live (read per-frame in capture -> no restart).

        Do this BEFORE calibrating: rotating after a fit invalidates the transform.
        """
        try:
            deg = int(payload.get("rotation_deg"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "rotation_deg must be one of 0, 90, 180, 270"}
        old = self.cfg.camera.rotation_deg
        self.cfg.camera.rotation_deg = deg     # in-place; PiCamCapture reads it each frame
        try:
            self.cfg.validate()
        except ConfigError as exc:
            self.cfg.camera.rotation_deg = old
            return {"ok": False, "error": str(exc)}
        logger.info("camera rotation set to %d deg", deg)
        return {"ok": True, "rotation_deg": deg}

    def _set_limits(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._disarmed():
            return {"ok": False, "error": "set limits only when disarmed"}
        s = self.cfg.servo
        keys = ("pan_min_deg", "pan_max_deg", "tilt_min_deg", "tilt_max_deg")
        old = {k: getattr(s, k) for k in keys}
        try:
            for k, v in payload.items():
                if k not in keys:
                    raise ConfigError(f"not a travel limit: {k!r}")
                setattr(s, k, float(v))        # in-place so the live ServoController sees it
            self.cfg.validate()
        except (ConfigError, ValueError, TypeError) as exc:
            for k, v in old.items():
                setattr(s, k, v)               # rollback
            return {"ok": False, "error": str(exc)}
        logger.info("travel limits set: %s", {k: getattr(s, k) for k in keys})
        return {"ok": True, "limits": {k: getattr(s, k) for k in keys}}

    def _cal_add(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cx, cy = float(payload["cx"]), float(payload["cy"])
        except (KeyError, ValueError, TypeError):
            return {"ok": False, "error": "add_sample needs numeric cx, cy"}
        pan = self.control.servo.last_angle(Axis.PAN)
        tilt = self.control.servo.last_angle(Axis.TILT)
        self._cal_samples.append((cx, cy, pan, tilt))
        return {"ok": True, "samples": len(self._cal_samples),
                "last": {"cx": round(cx, 1), "cy": round(cy, 1),
                         "pan": round(pan, 2), "tilt": round(tilt, 2)}}

    def _cal_fit(self) -> Dict[str, Any]:
        from aim.calibrate import fit_calibration
        if len(self._cal_samples) < 3:
            return {"ok": False, "error": f"need >= 3 samples (have {len(self._cal_samples)})"}
        pixels = [(c[0], c[1]) for c in self._cal_samples]
        pans = [c[2] for c in self._cal_samples]
        tilts = [c[3] for c in self._cal_samples]
        try:
            cal = fit_calibration(pixels, pans, tilts)
        except Exception as exc:  # noqa: BLE001 — surface fit error to the operator
            return {"ok": False, "error": f"fit failed: {exc}"}
        self.cfg.aim.pan_coeffs = list(cal.pan_coeffs)    # in-place; apply_config rebuilds cal
        self.cfg.aim.tilt_coeffs = list(cal.tilt_coeffs)
        self.control.apply_config()
        logger.info("calibration fitted from %d pts: pan=%s tilt=%s",
                    len(self._cal_samples), cal.pan_coeffs, cal.tilt_coeffs)
        return {"ok": True, "samples": len(self._cal_samples),
                "pan_coeffs": list(cal.pan_coeffs), "tilt_coeffs": list(cal.tilt_coeffs)}

    def save_config(self) -> Dict[str, Any]:
        from config import save_local_config
        try:
            delta = save_local_config(self.cfg)
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        logger.info("config saved (overlay sections: %s)", sorted(delta.keys()))
        return {"ok": True, "saved_sections": sorted(delta.keys())}


# --------------------------------------------------------------------------
# Bottle adapter (lazy import so the Mac test venv without bottle still imports)
# --------------------------------------------------------------------------

def _read_code(request) -> str:
    """v1 posts a raw text body; also accept JSON {'cmd': ...} / form 'cmd'."""
    try:
        data = request.json
    except Exception:  # noqa: BLE001 — malformed JSON body
        data = None
    if isinstance(data, dict) and "cmd" in data:
        return str(data["cmd"])
    raw = request.body.read().decode("utf-8", "ignore").strip()
    if raw:
        return raw
    return (request.forms.get("cmd") or "").strip()


def create_app(controller: TurretWebController, ui_path: Optional[str] = None):
    import bottle

    ui_file = ui_path or os.path.join(os.path.dirname(__file__), "web_ui.html")
    ui_dir, ui_name = os.path.dirname(ui_file), os.path.basename(ui_file)
    app = bottle.Bottle()

    def _maybe_error(result):
        if isinstance(result, dict) and result.get("ok") is False:
            bottle.response.status = 400
        return result

    @app.get("/")
    def index():
        return bottle.static_file(ui_name, root=ui_dir)

    @app.get("/api/telemetry")
    def telemetry():
        return controller.telemetry()

    @app.get("/api/turret-state")
    def turret_state():
        return controller.turret_state()

    @app.get("/api/config")
    def get_config():
        return controller.config_snapshot()

    @app.get("/api/detect-frame.jpg")
    def detect_frame():
        data = controller.detection_jpeg()
        if not data:
            bottle.response.status = 204            # disabled / no frame yet
            return ""
        bottle.response.content_type = "image/jpeg"
        bottle.response.set_header("Cache-Control", "no-store")
        return bytes(data)

    @app.post("/api/config")
    def post_config():
        body = bottle.request.json or {}
        return _maybe_error(
            controller.update_config(body.get("section"), body.get("changes") or {})
        )

    @app.post("/api/cmd")
    def cmd():
        return _maybe_error(controller.command(_read_code(bottle.request)))

    @app.post("/api/control-cmd")
    def control_cmd():
        return _maybe_error(controller.manual_control(_read_code(bottle.request)))

    @app.post("/api/calibrate")
    def calibrate():
        body = bottle.request.json or {}
        return _maybe_error(controller.calibrate(body.get("action"), body.get("payload")))

    return app


def serve(controller: TurretWebController, host: str = "0.0.0.0", port: int = 8001,
          ui_path: Optional[str] = None, quiet: bool = True) -> None:
    """Blocking Bottle server (run inside a daemon thread)."""
    import bottle

    bottle.run(create_app(controller, ui_path), host=host, port=port, quiet=quiet)


def start_web_thread(controller: TurretWebController, host: str = "0.0.0.0",
                     port: int = 8001, ui_path: Optional[str] = None):
    """Start the web server on a daemon thread; returns the thread."""
    import threading

    t = threading.Thread(
        target=serve, args=(controller, host, port), kwargs={"ui_path": ui_path},
        name="web", daemon=True,
    )
    t.start()
    return t
