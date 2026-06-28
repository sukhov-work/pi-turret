"""Unit tests for UsbStreamer (pure argv + lifecycle; subprocess injected).

The real spawn is Pi-only; here we inject a fake runner to assert the argv,
the enable/idempotent/stop gating, the LD_LIBRARY_PATH env, and that a spawn
failure is swallowed (streaming is non-critical).
"""
from __future__ import annotations

import os

import pytest

from app.streamer import UsbStreamer
from config import StreamConfig


class FakeProc:
    def __init__(self):
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False


@pytest.fixture
def recording_runner():
    calls = []

    def runner(argv, env):
        calls.append((argv, env))
        return FakeProc()

    runner.calls = calls
    return runner


def test_build_argv_uses_plugin_paths_and_flags():
    cfg = StreamConfig(device="/dev/video2", width_px=800, height_px=600, fps=20,
                       port=9090, plugin_dir="/opt/mjpg", www_dir="/opt/mjpg/www")
    argv = UsbStreamer(cfg).build_argv()
    assert argv[0] == "mjpg_streamer"
    assert argv[1] == "-i"
    assert argv[2] == "/opt/mjpg/input_uvc.so -d /dev/video2 -r 800x600 -f 20"
    assert argv[3] == "-o"
    assert argv[4] == "/opt/mjpg/output_http.so -p 9090 -w /opt/mjpg/www"


def test_build_argv_bare_plugin_when_no_dir_and_no_www():
    cfg = StreamConfig(plugin_dir="", www_dir="")
    argv = UsbStreamer(cfg).build_argv()
    assert argv[2].startswith("input_uvc.so ")     # bare name, no path
    assert argv[4] == "output_http.so -p 8080"       # no -w


def test_disabled_does_not_spawn(recording_runner):
    s = UsbStreamer(StreamConfig(enabled=False), runner=recording_runner)
    assert s.start() is False
    assert recording_runner.calls == []
    assert s.is_running() is False


def test_start_is_idempotent(recording_runner):
    s = UsbStreamer(StreamConfig(plugin_dir=""), runner=recording_runner)
    assert s.start() is True
    assert s.is_running() is True
    assert s.start() is True                          # already running
    assert len(recording_runner.calls) == 1          # spawned once only


def test_env_sets_ld_library_path(recording_runner):
    s = UsbStreamer(StreamConfig(plugin_dir="/opt/mjpg"), runner=recording_runner)
    s.start()
    _, env = recording_runner.calls[0]
    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[0] == "/opt/mjpg"


def test_stop_terminates(recording_runner):
    s = UsbStreamer(StreamConfig(plugin_dir=""), runner=recording_runner)
    s.start()
    proc = s._proc
    s.stop()
    assert proc.terminated is True
    assert s.is_running() is False


def test_start_failure_is_swallowed():
    def boom(argv, env):
        raise OSError("mjpg_streamer not found")

    s = UsbStreamer(StreamConfig(plugin_dir=""), runner=boom)
    assert s.start() is False                         # non-critical: no raise
    assert s.is_running() is False


def test_url():
    s = UsbStreamer(StreamConfig(port=8080))
    assert s.url("10.0.0.5") == "http://10.0.0.5:8080/?action=stream"
