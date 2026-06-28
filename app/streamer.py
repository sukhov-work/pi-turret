"""USB-webcam live stream via a separate mjpg-streamer process (Step 1.12).

The operator's live view comes from the **USB webcam**, encoded by an external
``mjpg_streamer`` process (UVC hardware-MJPEG passthrough) so the Pi spends **no
detection compute** on rendering — the Pi-Cam detection path is never streamed.
This mirrors v1's streaming approach (the rollback) without touching v1.

The argv builder and lifecycle gating are pure and unit-tested on the Mac; the
actual subprocess spawn is Pi-only and injected via ``runner`` for tests.
Streaming is **non-critical**: every failure is logged and swallowed so it can
never disturb the control loop.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable, List, Optional

from config import StreamConfig

logger = logging.getLogger(__name__)

# runner(argv, env) -> a process handle exposing poll()/terminate()/wait().
Runner = Callable[[List[str], dict], object]


def _default_runner(argv: List[str], env: dict):
    return subprocess.Popen(argv, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class UsbStreamer:
    """Manages an external mjpg-streamer process for the USB webcam."""

    def __init__(self, cfg: StreamConfig, runner: Optional[Runner] = None):
        self._cfg = cfg
        self._runner = runner or _default_runner
        self._proc = None

    def _plugin(self, name: str) -> str:
        """Full path to a plugin .so when a plugin_dir is set, else the bare name."""
        return os.path.join(self._cfg.plugin_dir, name) if self._cfg.plugin_dir else name

    def build_argv(self) -> List[str]:
        """Build the mjpg_streamer argv (no shell -> no injection)."""
        c = self._cfg
        input_spec = (f"{self._plugin(c.input_plugin)} -d {c.device} "
                      f"-r {c.width_px}x{c.height_px} -f {c.fps}")
        output_spec = f"{self._plugin(c.output_plugin)} -p {c.port}"
        if c.www_dir:
            output_spec += f" -w {c.www_dir}"
        return [c.binary, "-i", input_spec, "-o", output_spec]

    def _env(self) -> dict:
        env = os.environ.copy()
        if self._cfg.plugin_dir:  # let mjpg_streamer resolve plugin deps
            prev = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = (self._cfg.plugin_dir + os.pathsep + prev
                                      if prev else self._cfg.plugin_dir)
        return env

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """Spawn the streamer if enabled and not already running. Returns running state."""
        if not self._cfg.enabled:
            logger.info("usb stream disabled by config")
            return False
        if self.is_running():
            return True
        try:
            self._proc = self._runner(self.build_argv(), self._env())
            logger.info("usb stream started on :%s (%s)", self._cfg.port, self._cfg.device)
            return True
        except Exception:  # noqa: BLE001 — streaming is non-critical
            logger.warning("usb stream failed to start", exc_info=True)
            self._proc = None
            return False

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
        except Exception:  # noqa: BLE001
            logger.warning("usb stream stop failed", exc_info=True)
        finally:
            self._proc = None

    def url(self, host: str) -> str:
        return f"http://{host}:{self._cfg.port}/?action=stream"
