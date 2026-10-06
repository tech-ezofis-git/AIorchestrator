"""OCR tool — file bytes or blob path → Paddle extract_text → plain text, no LLM."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.config import Settings, get_settings
from app.integrations.ocr_engine import OcrEngineClient

logger = logging.getLogger("orchestrator.tools.ocr")

TOOL_ID = "ocr"
NO_INPUT_ERROR = "Upload a file or provide a blob filepath."
EMPTY_FILE_ERROR = "The uploaded file is empty."
EMPTY_TEXT_ERROR = "No text could be extracted from the document."


def _result(
    *,
    status: str,
    started: float,
    filename: Optional[str] = None,
    pages: Optional[str] = None,
    text: str = "",
    error: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "tool": TOOL_ID,
        "status": status,
        "filename": filename,
        "pages": pages,
        "text": text,
        "chars": len(text),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
    }


async def run_ocr_tool(
    engine: OcrEngineClient,
    *,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    filepath: Optional[str] = None,
    pageno: Optional[str] = None,
    layout: bool = True,
    tenant_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Run OCR on an uploaded file (`file_bytes`) or a blob `filepath`.

    Never raises: bad input and OCR failures come back as status FAILED with `error` set.
    `layout=True` keeps Paddle's layout spacing untouched; `False` strips the text.
    """
    started = time.perf_counter()
    settings = settings or get_settings()
    path = (filepath or "").strip() or None
    has_file = file_bytes is not None
    if not has_file and not path:
        return _result(status="FAILED", started=started, error=NO_INPUT_ERROR)

    try:
        pages = resolve_pageno(pageno, max_pages=settings.ocr_max_pages)
    except InvalidOcrPageError as exc:
        return _result(status="FAILED", started=started, error=str(exc))

    name = filename if has_file else path
    if has_file:
        if not file_bytes:
            return _result(status="FAILED", started=started, filename=name, error=EMPTY_FILE_ERROR)
        if len(file_bytes) > settings.ocr_max_file_bytes:
            limit_mb = settings.ocr_max_file_bytes // (1024 * 1024)
            return _result(
                status="FAILED", started=started, filename=name, error=f"The file is larger than {limit_mb} MB."
            )

    try:
        result = await engine.run_ocr(
            name or "document",
            filepath=None if has_file else path,
            file_bytes=file_bytes if has_file else None,
            filename=name,
            content_type=content_type if has_file else None,
            page_selection=pages,
            tenant_id=(tenant_id or "").strip() or None,
            layout=layout,
        )
    except Exception as exc:
        logger.warning("ocr_tool_failed", extra={"error_type": type(exc).__name__})
        reason = str(exc).strip() or type(exc).__name__
        return _result(
            status="FAILED", started=started, filename=name, pages=pages.label(), error=f"OCR failed: {reason}"
        )

    text = result.get("text") or ""
    if not layout:
        text = text.strip()
    name = result.get("filename") or name
    if not text.strip():
        return _result(status="FAILED", started=started, filename=name, pages=pages.label(), error=EMPTY_TEXT_ERROR)
    return _result(status="SUCCEEDED", started=started, filename=name, pages=pages.label(), text=text)
