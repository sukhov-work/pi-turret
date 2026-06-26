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
from typing import List

import numpy as np

from config import DetectorConfig
from contracts import Detection
from detect.base import Detector
from detect.decode import decode_v8
from errors import DetectionError

logger = logging.getLogger(__name__)


class CoralDetector(Detector):
    def __init__(self, cfg: DetectorConfig):
        self.cfg = cfg
        if cfg.backend.startswith("coral") and not cfg.model_path.endswith("_edgetpu.tflite"):
            logger.warning("model_path %s does not end _edgetpu.tflite — the Coral "
                           "will be bypassed and inference will run on CPU",
                           cfg.model_path)
        self._interpreter = None
        self._in_index = None
        self._out_index = None
        self._in_scale = 1.0
        self._in_zero = 0
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
            return decode_v8(
                raw, input_size_px=self.cfg.input_size_px,
                frame_width_px=frame.shape[1], frame_height_px=frame.shape[0],
                conf_threshold=self.cfg.conf_threshold,
                iou_threshold=self.cfg.iou_threshold,
                num_classes=self.cfg.num_classes,
                coords_normalized=self.cfg.coords_normalized,
            )
        except DetectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DetectionError("Coral inference failed") from exc

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        import cv2
        size = self.cfg.input_size_px
        if frame.ndim == 2:  # Y-plane greyscale -> 3 channels
            frame = np.stack([frame] * 3, axis=-1)
        if frame.shape[0] != size or frame.shape[1] != size:
            frame = cv2.resize(frame, (size, size))
        return np.expand_dims(frame.astype(np.uint8), axis=0)
