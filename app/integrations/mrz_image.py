"""MRZ band detection on rendered page images (OpenCV), ahead of OCR.

The MRZ is 2-3 long, dense, evenly spaced text lines. A blackhat filter keeps dark text on a light
background, a horizontal gradient plus wide closing smears each MRZ into one solid band, and bands
that are wide, short and span most of the document width are kept. Pages where nothing is found
are retried rotated 90 degrees (sideways scans); upside-down bands are resolved later, when the
crop's OCR text fails the check digits and is re-read rotated 180 degrees.

Never raises: any failure returns an empty list so OCR text extraction is unaffected.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import PageSelection
from app.integrations.qr_scan import _decode_image, _render_pdf_pages, is_scannable

logger = logging.getLogger("orchestrator.mrz_image")

_DETECT_WIDTH = 1200
# Band width / height: TD3 (2 x 44) is about 9-10, TD1 (3 x 30) about 4; long rules are far wider.
_MIN_ASPECT, _MAX_ASPECT = 3.0, 20.0
_MIN_WIDTH_FRACTION = 0.45
_MAX_BANDS_PER_PAGE = 2


def find_mrz_crops(
    data: bytes,
    *,
    filename: Optional[str],
    content_type: Optional[str],
    page_selection: PageSelection,
    dpi: int = 300,
    max_crops: int = 3,
) -> list[dict[str, Any]]:
    """Returns [{"page": n, "png": bytes}] for MRZ-like bands, most likely first."""
    if not data or not is_scannable(filename, content_type):
        return []
    try:
        import cv2
        import numpy  # noqa: F401
    except ImportError:
        logger.warning("mrz_image_unavailable_opencv_missing")
        return []

    try:
        is_pdf = (filename or "").lower().endswith(".pdf") or (content_type or "").lower() == "application/pdf"
        if is_pdf:
            pages = _render_pdf_pages(data, page_selection, dpi)
        else:
            pages = _decode_image(data) if page_selection.start == 1 else []

        crops: list[dict[str, Any]] = []
        for page_no, image in pages:
            bands = _detect_bands(image)
            if not bands:
                rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                bands = _detect_bands(rotated)
                image = rotated
            for box in bands:
                crops.append({"page": page_no, "png": _encode_png(_crop(image, box))})
                if len(crops) >= max_crops:
                    return crops
        return crops
    except Exception as exc:
        logger.warning("mrz_image_detect_failed", extra={"error_type": type(exc).__name__})
        return []


def rotate_png_180(png: bytes) -> bytes:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    return _encode_png(cv2.rotate(image, cv2.ROTATE_180))


def _detect_bands(image: Any) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x, y, w, h) in `image` coordinates, bottom-most first."""
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    scale = _DETECT_WIDTH / float(width)
    small = cv2.resize(image, (_DETECT_WIDTH, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (3, 3), 0)

    rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 7))
    square_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (41, 41))
    blackhat = cv2.morphologyEx(small, cv2.MORPH_BLACKHAT, rect_kernel)

    grad = np.absolute(cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1))
    low, high = float(grad.min()), float(grad.max())
    if high - low < 1e-6:
        return []
    grad = ((grad - low) / (high - low) * 255).astype("uint8")

    grad = cv2.morphologyEx(grad, cv2.MORPH_CLOSE, rect_kernel)
    _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, square_kernel)
    thresh = cv2.erode(thresh, None, iterations=4)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0:
            continue
        if _MIN_ASPECT <= w / h <= _MAX_ASPECT and w / _DETECT_WIDTH >= _MIN_WIDTH_FRACTION:
            boxes.append((x, y, w, h))
    boxes.sort(key=lambda b: b[1] + b[3], reverse=True)

    out = []
    for x, y, w, h in boxes[:_MAX_BANDS_PER_PAGE]:
        pad_x, pad_y = int(w * 0.04), int(h * 0.35)
        x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
        x1, y1 = min(_DETECT_WIDTH, x + w + pad_x), min(small.shape[0], y + h + pad_y)
        out.append((int(x0 / scale), int(y0 / scale), int((x1 - x0) / scale), int((y1 - y0) / scale)))
    return out


def _crop(image: Any, box: tuple[int, int, int, int]) -> Any:
    import cv2

    x, y, w, h = box
    crop = image[y:y + h, x:x + w]
    if crop.shape[0] < 120:
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _encode_png(image: Any) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", image)
    return buf.tobytes() if ok else b""
