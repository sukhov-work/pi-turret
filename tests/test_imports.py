"""Every v2 module must import on the Mac with NO hardware side effects.

This is the import-time-execution guard: v1's TurretHandler ran a full hardware
init + detection loop at import. If any module here touches I2C/GPIO/camera at
import (instead of behind start()/__main__), it will fail to import on the Mac.
"""
import importlib

import pytest

MODULES = [
    "errors", "contracts", "config", "capture", "main",
    "detect", "detect.base", "detect.decode", "detect.coral",
    "track", "track.tracker", "track.predict",
    "strategy", "strategy.scoring", "strategy.selector",
    "aim", "aim.calibrate", "aim.controller", "aim.killzone",
    "actuate", "actuate.pca9685", "actuate.servo", "actuate.pump",
    "actuate.lcd", "actuate.indicators",
    "app", "app.statemachine", "app.control", "app.pipeline",
    "app.annotate", "app.snapshots", "app.display", "app.remote",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports_cleanly(name):
    assert importlib.import_module(name) is not None
