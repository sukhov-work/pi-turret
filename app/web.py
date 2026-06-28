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
from dataclasses import asdict, fields
from typing import Any, Dict, Optional

from actuate.servo import Axis
from config import _SECTIONS
from errors import ConfigError

logger = logging.getLogger(__name__)

# Sections the web UI may tune at runtime. Hardware/driver sections (servo pins,
# pump GPIO, camera, detector model, remote) are deliberately excluded — changing
# them live is unsafe or needs a restart.
EDITABLE_SECTIONS = (
    "strategy", "killzone", "predict", "controller", "fire", "aim", "tracker", "app", "stream",
)

_JOG = {
    "up": (Axis.TILT, +1),
    "down": (Axis.TILT, -1),
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
        # Geometry for the tactical display (detection-frame pixel space).
        out["frame"] = {"w": int(self.cfg.camera.capture_width_px),
                        "h": int(self.cfg.camera.capture_height_px)}
        out["killzone"] = asdict(self.cfg.killzone)
        return out

    def config_snapshot(self) -> Dict[str, Any]:
        return {name: asdict(getattr(self.cfg, name)) for name in EDITABLE_SECTIONS}

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
        elif code in ("enable_aux", "enable_aux_laser"):
            self.cfg.app.aux_marker_enabled = True
            self.control.apply_config()
        elif code in ("disable_aux", "disable_aux_laser"):
            self.cfg.app.aux_marker_enabled = False
            self.control.apply_config()
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

        self.control.apply_config()  # refresh init-snapshotted values
        logger.info("config updated: %s <- %s", section, changes)
        return {"ok": True, "section": section, "values": asdict(new_section)}


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
