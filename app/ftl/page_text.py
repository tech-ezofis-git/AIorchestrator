"""Per-page RFQ text for the qualifier and quote estimator.

Selectable PDF text is kept as-is. Only pages that are images or scans (almost
no text layer) are sent to the Paddle extract API, one page at a time.
"""

from __future__ import annotations

import logging
from typing import List

from app.agents.ocr_helpers import PageSelection
from app.config import get_settings
from app.integrations.ocr_engine import OcrEngineClient, OcrEngineError

logger = logging.getLogger("orchestrator.ftl_page_text")

# A real spec page is far above this. A page number or running header is not.
_MIN_ALNUM = 40

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_ONE_PAGE = PageSelection(start=1, end=1, raw="1")


def file_extension(filename: str) -> str:
    lower = (filename or "").lower()
    if "." not in lower:
        return ""
    return "." + lower.rsplit(".", 1)[-1]


def is_image_filename(filename: str) -> bool:
    return file_extension(filename) in IMAGE_EXTENSIONS


def prefer_spec_attachment(attachments: List[dict]):
    """PDF or DOCX wins over a logo image that happens to be attached first."""
    ranked = []
    for attachment in attachments:
        name = attachment.get("filename") or ""
        ext = file_extension(name)
        if ext == ".pdf":
            rank = 0
        elif ext == ".docx":
            rank = 1
        elif ext in IMAGE_EXTENSIONS:
            rank = 2
        else:
            continue
        ranked.append((rank, attachment))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def text_is_usable(text: str) -> bool:
    return sum(ch.isalnum() for ch in (text or "")) >= _MIN_ALNUM


def _page_has_images(page) -> bool:
    try:
        if page.get_images():
            return True
    except Exception:
        pass
    try:
        return bool(page.get_image_info())
    except Exception:
        return False


def _single_page_pdf(doc, page_index: int) -> bytes:
    import pymupdf as fitz

    single = fitz.open()
    try:
        single.insert_pdf(doc, from_page=page_index, to_page=page_index)
        return single.tobytes()
    finally:
        single.close()


async def _ocr_bytes(data: bytes, filename: str, content_type: str) -> str:
    settings = get_settings()
    url = (settings.ocr_extract_url or "").strip()
    if not url or not data:
        return ""
    client = OcrEngineClient(settings)
    try:
        return await client._call_extract_text(
            url=url,
            engine=settings.ocr_engine or "paddle",
            data=data,
            filename=filename,
            content_type=content_type,
            page_selection=_ONE_PAGE,
            timeout=settings.ocr_download_timeout_seconds,
        )
    except OcrEngineError:
        logger.warning("ftl_page_ocr_failed", extra={"filename": filename})
        return ""
    except Exception:
        logger.warning("ftl_page_ocr_failed", extra={"filename": filename}, exc_info=False)
        return ""


async def ocr_image_file(data: bytes, filename: str) -> str:
    ext = file_extension(filename) or ".png"
    content_type = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
    name = filename or f"page{ext}"
    return await _ocr_bytes(data, name, content_type)


async def extract_pdf_text_hybrid(pdf_bytes: bytes) -> str:
    """Text pages stay local. Image or scan pages go to Paddle, up to ftl_ocr_max_pages."""
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None

    if fitz is None:
        return ""

    settings = get_settings()
    cap = max(0, int(settings.ftl_ocr_max_pages))
    pages: List[str] = []
    ocr_used = 0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for index, page in enumerate(doc):
            local = (page.get_text("text") or "").strip()
            if text_is_usable(local) or not _page_has_images(page):
                pages.append(local)
                continue
            if ocr_used >= cap:
                logger.warning(
                    "ftl_page_ocr_cap",
                    extra={"cap": cap, "page": index + 1},
                )
                pages.append(local)
                continue
            ocr_used += 1
            page_bytes = _single_page_pdf(doc, index)
            ocr_text = (await _ocr_bytes(page_bytes, f"page-{index + 1}.pdf", "application/pdf")).strip()
            pages.append(ocr_text or local)
    finally:
        doc.close()

    return "\n\n".join(pages)
