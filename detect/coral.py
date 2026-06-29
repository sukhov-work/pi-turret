"""Edge-TPU YOLOv8 detector backend (Pi-only runtime; imports clean on the Mac).

Loads an INT8 ``*_edgetpu.tflite`` model via the Coral delegate and decodes with
the anchor-free ``decode_v8`` (NO YOLOv5 path). The compiled file MUST end
``_edgetpu.tflite`` or the runtime silently falls back to CPU and the Coral is
bypassed. Build/compile the model on the Strix Halo box only.

All heavy imports (tflite_runtime / pycoral) are lazy so the module imports on the
Mac for type-checking and wiring; actual inference is Pi-only truth.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

import numpy as np

from config import DetectorConfig
from contracts import Detection
from detect.base import Detector
from detect.decode import decode_v8
from errors import DetectionError

logger = logging.getLogger(__name__)


class CoralDetector(Detector):
    def __init__(self, cfg: DetectorConfig, frame_width_px: Optional[int] = None,
                 frame_height_px: Optional[int] = None):
        self.cfg = cfg
        # Full-frame coordinate space the detections map back to (the space the
        # killzone, scoring, calibration and the tactical canvas all use). The
        # detector runs on the small lores frame (e.g. 256px) but its output must
        # land in capture-frame pixels (e.g. 1152px). When unset, falls back to the
        # input frame's own size (bench/no-camera use).
        self._frame_width_px = frame_width_px
        self._frame_height_px = frame_height_px
        if cfg.backend.startswith("coral") and "_edgetpu" not in os.path.basename(cfg.model_path):
            logger.warning("model_path %s has no _edgetpu marker — verify it is the Edge-TPU-"
                           "compiled model, not a plain tflite (which would run on CPU)",
                           cfg.model_path)
        self._interpreter = None
        self._in_index = None
        self._out_index = None
        self._in_scale = 1.0
        self._in_zero = 0
        self._in_dtype = np.uint8
        self._out_scale = 1.0
        self._out_zero = 0

    def load(self) -> None:
        try:
            try:
                from pycoral.utils.edgetpu import make_interpreter
                interpreter = make_interpreter(self.cfg.model_path)
            except ImportError:
                import tflite_runtime.interpreter as tflite
                interpreter = tflite.Interpreter(
                    model_path=self.cfg.model_path,
                    experimental_delegates=[tflite.load_delegate("libedgetpu.so.1")],
                )
            interpreter.allocate_tensors()
            in_det = interpreter.get_input_details()[0]
            out_det = interpreter.get_output_details()[0]
            self._interpreter = interpreter
            self._in_index = in_det["index"]
            self._out_index = out_det["index"]
            self._in_scale, self._in_zero = in_det["quantization"]
            self._in_dtype = in_det["dtype"]
            self._out_scale, self._out_zero = out_det["quantization"]
        except Exception as exc:  # noqa: BLE001
            raise DetectionError(f"failed to load Coral model {self.cfg.model_path}") from exc

    def infer(self, frame: np.ndarray) -> List[Detection]:
        if self._interpreter is None:
            self.load()
        try:
            tensor = self._preprocess(frame)
            self._interpreter.set_tensor(self._in_index, tensor)
            self._interpreter.invoke()
            raw = self._interpreter.get_tensor(self._out_index)
            if self._out_scale:  # dequantize INT8 -> float
                raw = (raw.astype(np.float32) - self._out_zero) * self._out_scale
            fw, fh = self._frame_dims(frame)
            return decode_v8(
                raw, input_size_px=self.cfg.input_size_px,
                frame_width_px=fw, frame_height_px=fh,
                conf_threshold=self.cfg.conf_threshold,
                iou_threshold=self.cfg.iou_threshold,
                num_classes=self.cfg.num_classes,
                coords_normalized=self.cfg.coords_normalized,
            )
        except DetectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DetectionError("Coral inference failed") from exc

    def _frame_dims(self, frame: np.ndarray) -> Tuple[int, int]:
        """Full-frame (width, height) the detections map to (configured, else frame)."""
        return (self._frame_width_px or frame.shape[1],
                self._frame_height_px or frame.shape[0])

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        import cv2
        size = self.cfg.input_size_px
        if frame.ndim == 2:  # Y-plane greyscale -> 3 channels
            frame = np.stack([frame] * 3, axis=-1)
        if frame.shape[0] != size or frame.shape[1] != size:
            frame = cv2.resize(frame, (size, size))
        # The model wants normalized [0,1] input; full-INT8 exports fold that /255 into the
        # input quantization, so quantize per the tensor's own (scale, zero) + dtype. Feeding
        # raw uint8 fails on int8-input edgetpu models (Ultralytics exports use zero_point=-128).
        x = frame.astype(np.float32) / 255.0
        if self._in_scale:
            x = x / self._in_scale + self._in_zero
        if np.issubdtype(self._in_dtype, np.integer):
            info = np.iinfo(self._in_dtype)
            x = np.clip(np.round(x), info.min, info.max)
        return np.expand_dims(x.astype(self._in_dtype), axis=0)
