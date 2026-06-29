"""Detection-cam debug view: JPEG-encode the raw lores frame (Pi-side, lazy cv2).

The web canvas draws boxes / aim / kill-zone / HUD *on top* of this image, so the
Pi only has to encode the greyscale frame the detector actually sees — no server-
side drawing. Gated + rate-capped by the caller because the encode competes with
detection compute. ``encode_jpeg`` returns ``None`` on any failure (non-critical).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def encode_jpeg(frame: np.ndarray, quality: int = 70) -> Optional[bytes]:
    """JPEG-encode a detection frame (greyscale or BGR). None on failure."""
    try:
        import cv2  # lazy: Pi-side
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return buf.tobytes() if ok else None
    except Exception:  # noqa: BLE001 — debug view is non-critical
        logger.warning("detection-frame JPEG encode failed", exc_info=True)
        return None
