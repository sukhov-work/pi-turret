"""Camera capture abstractions. Hardware imports are lazy (Pi-only truth).

Two cameras, two roles (never crossed): the **Pi Camera** is the detection source
(picamera2 lores **YUV420** on Pi 4 — RGB lores is Pi 5 only — sized to the model
input to avoid a resize cost); the **USB webcam** is the live-stream source. The
detection path reads the Y plane (greyscale) at model-input resolution.
"""
from __future__ import annotations

import abc

import numpy as np

from config import CameraConfig
from errors import CameraError


class FrameSource(abc.ABC):
    @abc.abstractmethod
    def read_frame(self) -> np.ndarray:
        """Return the latest frame as an ndarray (detection path = Y-plane greyscale)."""

    def close(self) -> None:
        pass


class PiCamCapture(FrameSource):
    """picamera2 lores YUV420 detection source, sized to the model input."""

    def __init__(self, cfg: CameraConfig, input_size_px: int):
        self._cfg = cfg
        self._size = input_size_px
        self._picam = None

    def start(self) -> None:
        try:
            from picamera2 import Picamera2  # lazy: Pi-only
            picam = Picamera2()
            config = picam.create_preview_configuration(
                lores={"format": self._cfg.lores_format,
                       "size": (self._size, self._size)},
                main={"format": "RGB888",
                      "size": (self._cfg.capture_width_px, self._cfg.capture_height_px)},
            )
            picam.configure(config)
            if self._cfg.fixed_focus:
                # Manual focus (AfMode 0): no AF hunting on a moving target.
                picam.set_controls({"AfMode": 0, "LensPosition": self._cfg.lens_position})
            picam.start()
            self._picam = picam
        except Exception as exc:  # noqa: BLE001
            raise CameraError("picamera2 init failed") from exc

    def read_frame(self) -> np.ndarray:
        if self._picam is None:
            raise CameraError("camera not started")
        try:
            yuv = self._picam.capture_array("lores")
            # YUV420 planar: the first H rows are the Y (luma) plane.
            return yuv[: self._size, : self._size]
        except Exception as exc:  # noqa: BLE001
            raise CameraError("picamera2 capture failed") from exc

    def close(self) -> None:
        if self._picam is not None:
            self._picam.close()
            self._picam = None


class UsbCapture(FrameSource):
    """USB webcam source (streaming / spotter). Not the detection path."""

    def __init__(self, device_index: int = 0):
        self._index = device_index
        self._cap = None

    def start(self) -> None:
        try:
            import cv2  # lazy
            self._cap = cv2.VideoCapture(self._index)
        except Exception as exc:  # noqa: BLE001
            raise CameraError("usb camera init failed") from exc

    def read_frame(self) -> np.ndarray:
        if self._cap is None:
            raise CameraError("usb camera not started")
        ok, frame = self._cap.read()
        if not ok:
            raise CameraError("usb camera read failed")
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
