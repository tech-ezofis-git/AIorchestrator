"""QR code detection and decoding for OCR document jobs (OpenCV).

Renders the selected PDF pages (or the uploaded image) to grayscale images and runs OpenCV's
QRCodeDetectorAruco (fast) with QRCodeDetector as fallback on each page in parallel. Pages with no
decoded QR are retried once at double resolution, since invoice QR codes are often small. Known
payloads get a `decoded` object:

- GST e-invoice QR: a NIC-signed JWT whose `data` claim holds the invoice summary
  (SellerGstin, BuyerGstin, DocNo, DocDt, TotInvVal, Irn, ...).
- UPI payment QR: `upi://pay?pa=...&am=...` query parameters.

Never raises: any failure (OpenCV missing, unreadable file) returns an empty list so OCR text
extraction is unaffected.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit

from app.agents.ocr_helpers import PageSelection

logger = logging.getLogger("orchestrator.qr_scan")

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
_MAX_WORKERS = 8


def is_scannable(filename: Optional[str], content_type: Optional[str]) -> bool:
    name = (filename or "").strip().lower()
    ctype = (content_type or "").strip().lower()
    return (
        name.endswith(".pdf")
        or ctype == "application/pdf"
        or name.endswith(_IMAGE_EXTENSIONS)
        or ctype.startswith("image/")
    )


def scan_document_qr(
    data: bytes,
    *,
    filename: Optional[str],
    content_type: Optional[str],
    page_selection: PageSelection,
    dpi: int = 200,
) -> list[dict[str, Any]]:
    """Returns [{"page": 3, "data": "...", "decoded": {...}}] for every QR found, in page order."""
    if not data or not is_scannable(filename, content_type):
        return []
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        logger.warning("qr_scan_unavailable_opencv_missing")
        return []

    try:
        is_pdf = (filename or "").lower().endswith(".pdf") or (content_type or "").lower() == "application/pdf"
        if is_pdf:
            pages = _render_pdf_pages(data, page_selection, dpi)
        else:
            pages = _decode_image(data) if page_selection.start == 1 else []
        if not pages:
            return []

        found = _scan_pages(pages)

        # Small dense codes are often not even detected at base resolution: retry misses at double
        # resolution, fully where a QR was seen, fast-only elsewhere to keep blank pages cheap.
        missed = {page_no for page_no, _ in pages if not found[page_no][0]}
        seen = {page_no for page_no in missed if found[page_no][1]}
        retry: dict[int, tuple[list[str], bool]] = {}
        if missed:
            if is_pdf:
                retry_pages = _render_pdf_pages(data, page_selection, dpi * 2, only=missed)
            else:
                retry_pages = [(page_no, _upscale(image)) for page_no, image in pages]
            retry = _scan_pages(retry_pages, thorough=seen)

        hits: list[dict[str, Any]] = []
        for page_no, _ in pages:
            texts = found[page_no][0] or retry.get(page_no, ([], False))[0]
            for text in texts:
                hit: dict[str, Any] = {"page": page_no, "data": text}
                decoded = decode_qr_payload(text)
                if decoded is not None:
                    hit["decoded"] = decoded
                hits.append(hit)
        return hits
    except Exception as exc:
        logger.warning("qr_scan_failed", extra={"error_type": type(exc).__name__})
        return []


def _scan_pages(
    pages: list[tuple[int, Any]], thorough: Optional[set[int]] = None
) -> dict[int, tuple[list[str], bool]]:
    """Scans pages in parallel; `thorough` limits the full scan to those page numbers."""
    if not pages:
        return {}
    workers = max(1, min(_MAX_WORKERS, os.cpu_count() or 1, len(pages)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(
            lambda item: _scan_image(item[1], thorough is None or item[0] in thorough), pages
        )
        return dict(zip((page_no for page_no, _ in pages), results))


def _render_pdf_pages(
    data: bytes, page_selection: PageSelection, dpi: int, only: Optional[set[int]] = None
) -> list[tuple[int, Any]]:
    import fitz
    import numpy as np

    images: list[tuple[int, Any]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        last = min(page_selection.end, doc.page_count)
        for page_no in range(max(page_selection.start, 1), last + 1):
            if only is not None and page_no not in only:
                continue
            pix = doc.load_page(page_no - 1).get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            images.append((page_no, image.copy()))
    return images


def _decode_image(data: bytes) -> list[tuple[int, Any]]:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    return [(1, image)] if image is not None else []


def _upscale(image: Any) -> Any:
    import cv2

    return cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)


def _scan_image(image: Any, thorough: bool = True) -> tuple[list[str], bool]:
    """Returns (decoded texts, whether any QR-like region was detected at all).

    thorough=False runs only the fast Aruco single-code pass (the slow QRCodeDetector is skipped).
    """
    import cv2

    detected = False
    if hasattr(cv2, "QRCodeDetectorAruco"):
        aruco = cv2.QRCodeDetectorAruco()
        try:
            if thorough:
                ok, decoded, points, _ = aruco.detectAndDecodeMulti(image)
                detected = points is not None and len(points) > 0
                texts = [t for t in decoded if t] if ok else []
            else:
                single, points, _ = aruco.detectAndDecode(image)
                detected = points is not None
                texts = [single] if single else []
            if texts:
                return list(dict.fromkeys(texts)), True
        except cv2.error:
            pass
        if not thorough:
            return [], detected
    try:
        single, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
        detected = detected or points is not None
        if single:
            return [single], True
    except cv2.error:
        pass
    return [], detected


def decode_qr_payload(text: str) -> Optional[dict[str, Any]]:
    value = (text or "").strip()
    if value.lower().startswith("upi://"):
        params = dict(parse_qsl(urlsplit(value).query))
        return {"type": "upi", **params} if params else None
    claims = _jwt_claims(value)
    if claims is None:
        return None
    inner = claims.get("data")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            pass
    if isinstance(inner, dict):
        return {"type": "gst_einvoice", "issuer": claims.get("iss"), **inner}
    return {"type": "jwt", **claims}


def _jwt_claims(value: str) -> Optional[dict[str, Any]]:
    parts = value.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    return claims if isinstance(claims, dict) else None
