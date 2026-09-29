"""Local MRZ reading with PassportEye (MIT) + Tesseract (Apache-2.0).

PassportEye locates the MRZ lines inside each OpenCV band crop (`mrz_image.find_mrz_crops`) and
reads them with Tesseract; the lines are then validated by `mrz_parse.find_mrz` (structure and
check digits), so every MRZ source shares the same parser and output shape.

Never raises: returns None when PassportEye or the tesseract binary is unavailable.
"""
from __future__ import annotations

import io
import logging
import warnings
from typing import Any, Optional

from app.integrations.mrz_image import rotate_png_180
from app.integrations.mrz_parse import find_mrz

logger = logging.getLogger("orchestrator.ocr")


def read_mrz_crops(crops: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The first fully valid MRZ read from the crops (each upright, then rotated 180 degrees),
    else the partial read with the most passing check digits."""
    try:
        from passporteye import read_mrz
    except ImportError:
        return None
    best: Optional[dict[str, Any]] = None
    for crop in crops:
        for rotated in (False, True):
            png = rotate_png_180(crop["png"]) if rotated else crop["png"]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    result = read_mrz(io.BytesIO(png))
            except Exception as exc:
                logger.warning("mrz_passporteye_failed", extra={"error_type": type(exc).__name__, "error": str(exc)[:200]})
                return best
            raw_text = (result.aux.get("raw_text") or "") if result is not None else ""
            mrz = find_mrz(raw_text)
            if mrz is None:
                continue
            mrz = {**mrz, "source": "passporteye", "page": crop["page"]}
            if mrz["valid"]:
                return mrz
            if best is None or sum(mrz["checks"].values()) > sum(best["checks"].values()):
                best = mrz
    return best
