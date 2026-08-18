"""OCR engine client — download blob/upload bytes, call Azure extract_text.

When OCR_EXTRACT_URL is empty, falls back to a deterministic mock so unit
tests can run without the remote OCR service.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.agents.ocr_helpers import (
    BlobRef,
    InvalidBlobPathError,
    PageSelection,
    filename_from_blob_name,
    guess_content_type,
    parse_blob_filepath,
)
from app.config import Settings
from app.integrations.docx_text import (
    DocxExtractError,
    extract_docx_text,
    looks_like_docx,
    looks_like_legacy_doc,
)

logger = logging.getLogger("orchestrator.ocr")


class OcrEngineError(Exception):
    """Raised when blob download or extract_text fails."""


class OcrEngineClient:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings

    def _cfg(self) -> Settings:
        if self._settings is None:
            from app.config import get_settings

            return get_settings()
        return self._settings

    async def run_ocr(
        self,
        reference: str = "",
        *,
        filepath: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        page_selection: Optional[PageSelection] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Extract text for a document job or legacy reference string.

        Prefer file_bytes (upload) over filepath (blob). Legacy callers may
        still pass only `reference` (mock/demo path when URL unset).
        """
        settings = self._cfg()
        pages = page_selection or PageSelection(start=1, end=1, raw="1")
        source = filepath or reference or filename or "document"
        extract_url = (settings.ocr_extract_url or "").strip()

        if looks_like_legacy_doc(filename, content_type, filepath):
            raise OcrEngineError(
                "Legacy .doc is not supported. Upload a .docx or PDF, or paste OCR text."
            )

        if looks_like_docx(filename, content_type, filepath):
            data, name, _ctype = await self._resolve_bytes(
                filepath=filepath,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                reference=reference,
                tenant_id=tenant_id,
            )
            try:
                text = extract_docx_text(data)
            except DocxExtractError as exc:
                raise OcrEngineError(str(exc)) from exc
            return {
                "source_reference": source,
                "text": text,
                "confidence": None,
                "mock": False,
                "filename": name,
                "pages": "docx",
            }

        data, name, ctype = await self._resolve_bytes(
            filepath=filepath,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            reference=reference,
            tenant_id=tenant_id,
        )

        local_text = self._extract_local_text(
            data=data,
            filename=name,
            content_type=ctype,
            page_selection=pages,
        )
        if local_text:
            return {
                "source_reference": source,
                "text": local_text,
                "confidence": None,
                "mock": False,
                "filename": name,
                "pages": pages.label(),
            }

        # Offline / unit-test mock: no remote OCR URL configured.
        if not extract_url:
            return self._mock_result(
                source_reference=source,
                page_selection=pages,
                filename=name or "document.bin",
            )

        if not data:
            raise OcrEngineError("No file bytes available for OCR.")

        try:
            text = await self._call_extract_text(
                url=extract_url,
                engine=settings.ocr_engine or "paddle",
                data=data,
                filename=name,
                content_type=ctype,
                page_selection=pages,
                timeout=settings.ocr_download_timeout_seconds,
            )
        except OcrEngineError:
            raise
        except Exception as exc:
            logger.warning("ocr_extract_failed", extra={"error_type": type(exc).__name__})
            raise OcrEngineError("OCR extract_text request failed.") from exc

        return {
            "source_reference": source,
            "text": text,
            "confidence": None,
            "mock": False,
            "filename": name,
            "pages": pages.label(),
        }

    def _extract_local_text(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        page_selection: PageSelection,
    ) -> Optional[str]:
        lowered = (filename or "").strip().lower()
        ctype = (content_type or "").strip().lower()

        if lowered.endswith(".txt") or ctype.startswith("text/plain"):
            text = data.decode("utf-8-sig", errors="replace").strip()
            return text or None

        if lowered.endswith(".pdf") or ctype == "application/pdf":
            return _extract_pdf_text(data, page_selection=page_selection)

        return None

    async def _resolve_bytes(
        self,
        *,
        filepath: Optional[str],
        file_bytes: Optional[bytes],
        filename: Optional[str],
        content_type: Optional[str],
        reference: str,
        tenant_id: Optional[str] = None,
    ) -> tuple[bytes, str, str]:
        settings = self._cfg()
        max_bytes = settings.ocr_max_file_bytes

        if file_bytes is not None:
            if len(file_bytes) == 0:
                raise OcrEngineError("Uploaded file is empty.")
            if len(file_bytes) > max_bytes:
                raise OcrEngineError("Uploaded file exceeds size limit.")
            name = filename or "upload.bin"
            return file_bytes, name, content_type or guess_content_type(name)

        path = (filepath or reference or "").strip()
        if not path:
            raise OcrEngineError("No file or filepath provided for OCR.")

        # Legacy mock reference (SCN-42) when no extract URL and no blob path shape
        extract_url = (settings.ocr_extract_url or "").strip()
        if not extract_url and "/" not in path.replace("\\", "/") and not path.lower().startswith("http"):
            # Deterministic placeholder for old unit tests without remote OCR.
            return b"", path, "application/octet-stream"

        suffixes = [s.strip() for s in (settings.ocr_allowed_host_suffixes or "").split(",") if s.strip()]
        try:
            blob_ref = parse_blob_filepath(
                path,
                allowed_host_suffixes=suffixes or [".blob.core.windows.net"],
                tenant_id=tenant_id,
                container_prefix=settings.azure_blob_container_prefix,
            )
        except InvalidBlobPathError as exc:
            raise OcrEngineError(str(exc)) from exc

        data = await self._download_blob(blob_ref, max_bytes=max_bytes, timeout=settings.ocr_download_timeout_seconds)
        name = filename or filename_from_blob_name(blob_ref.blob_name)
        return data, name, content_type or guess_content_type(name)

    async def _download_blob(self, blob_ref: BlobRef, *, max_bytes: int, timeout: float) -> bytes:
        settings = self._cfg()
        conn = (settings.azure_storage_connection_string or "").strip()
        if conn:
            return await self._download_via_azure_sdk(conn, blob_ref, max_bytes=max_bytes)

        # Public/SAS HTTP fallback for allowlisted hosts only
        if not blob_ref.account_url_host:
            raise OcrEngineError(
                "AZURE_STORAGE_CONNECTION_STRING is required for relative blob paths."
            )
        url = f"https://{blob_ref.account_url_host}/{blob_ref.container}/{blob_ref.blob_name}"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.content
        except Exception as exc:
            logger.warning("blob_http_download_failed", extra={"error_type": type(exc).__name__})
            raise OcrEngineError("Failed to download blob.") from exc
        if len(data) > max_bytes:
            raise OcrEngineError("Downloaded blob exceeds size limit.")
        if not data:
            raise OcrEngineError("Downloaded blob is empty.")
        return data

    async def _download_via_azure_sdk(
        self, connection_string: str, blob_ref: BlobRef, *, max_bytes: int
    ) -> bytes:
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ImportError as exc:
            raise OcrEngineError("azure-storage-blob is not installed.") from exc

        try:
            service = BlobServiceClient.from_connection_string(connection_string)
            async with service:
                blob = service.get_blob_client(blob_ref.container, blob_ref.blob_name)
                stream = await blob.download_blob()
                data = await stream.readall()
        except OcrEngineError:
            raise
        except Exception as exc:
            logger.warning("blob_sdk_download_failed", extra={"error_type": type(exc).__name__})
            raise OcrEngineError("Failed to download blob from Azure Storage.") from exc

        if len(data) > max_bytes:
            raise OcrEngineError("Downloaded blob exceeds size limit.")
        if not data:
            raise OcrEngineError("Downloaded blob is empty.")
        return data

    async def _call_extract_text(
        self,
        *,
        url: str,
        engine: str,
        data: bytes,
        filename: str,
        content_type: str,
        page_selection: PageSelection,
        timeout: float,
    ) -> str:
        if not data and not (self._cfg().ocr_extract_url or "").strip():
            return ""

        files = {"file": (filename, data, content_type)}
        form = {"engine": engine}
        # Best-effort page hints if the remote API accepts them
        if page_selection.is_range:
            form["pageno"] = "-1"
            form["max_pages"] = str(page_selection.end)
        else:
            form["pageno"] = str(page_selection.start)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, data=form, files=files)
                response.raise_for_status()
                content_type_hdr = response.headers.get("content-type", "")
                body_text = response.text
                payload = response.json() if content_type_hdr.startswith("application/json") else None
        except httpx.HTTPError as exc:
            raise OcrEngineError("OCR extract_text request failed.") from exc

        text = _extract_text_from_response(payload, response_text=body_text)
        if text is None:
            raise OcrEngineError("OCR extract_text returned no text.")
        return text

    def _mock_result(
        self, *, source_reference: str, page_selection: PageSelection, filename: str
    ) -> dict[str, Any]:
        import hashlib

        digest = hashlib.sha256(source_reference.encode()).hexdigest()
        fraction = int(digest[:8], 16) / 0xFFFFFFFF
        lowered = source_reference.lower()
        if any(m in lowered for m in ("blurry", "low-quality", "low_quality", "scan-error")):
            confidence = round(0.30 + fraction * 0.25, 2)
        else:
            confidence = round(0.75 + fraction * 0.24, 2)
        return {
            "source_reference": source_reference,
            "text": (
                f"Placeholder OCR text extracted from '{source_reference}' "
                f"({page_selection.label()}, file={filename}). "
                "Invoice No INV/26-27/002140 Due Date 2026-05-20."
            ),
            "confidence": confidence,
            "mock": True,
            "filename": filename,
            "pages": page_selection.label(),
        }


def _extract_text_from_response(payload: Any, *, response_text: str) -> Optional[str]:
    if payload is None:
        text = (response_text or "").strip()
        return text or None
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        for key in ("text", "ocr_text", "content", "result", "data"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = _extract_text_from_response(value, response_text="")
                if nested:
                    return nested
            if isinstance(value, list):
                parts = [str(x) for x in value if x]
                if parts:
                    return "\n".join(parts)
        # pages: [{text: ...}]
        pages = payload.get("pages")
        if isinstance(pages, list):
            parts = []
            for page in pages:
                if isinstance(page, dict):
                    t = page.get("text") or page.get("markdown") or page.get("content")
                    if isinstance(t, dict):
                        t = t.get("text")
                    if t:
                        parts.append(str(t))
                elif isinstance(page, str):
                    parts.append(page)
            if parts:
                return "\n".join(parts)
    return None


def _extract_pdf_text(data: bytes, *, page_selection: PageSelection) -> Optional[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.page_count <= 0:
                return None
            start = max(page_selection.start - 1, 0)
            end = min(page_selection.end, doc.page_count)
            parts: list[str] = []
            for idx in range(start, end):
                text = (doc.load_page(idx).get_text("text") or "").strip()
                if text:
                    parts.append(text)
    except Exception:
        logger.warning("pdf_local_extract_failed", exc_info=False)
        return None

    merged = "\n\n".join(parts).strip()
    return merged or None
