"""run_ocr tool — Dispatcher wrapper around OcrEngineClient."""
from typing import Any, Optional

from app.agents.ocr_helpers import PageSelection
from app.integrations.ocr_engine import OcrEngineClient
from app.models.tool_schema import ToolSchema

RUN_OCR_SCHEMA = ToolSchema(
    name="run_ocr",
    description="Extract text from a document/image via OCR (upload bytes or blob filepath).",
    parameters={
        "type": "object",
        "properties": {
            "reference": {"type": "string", "description": "Legacy reference or display label."},
            "filepath": {"type": "string", "description": "Blob URL or folder/file path (container ezts{tenantid})."},
            "tenant_id": {"type": "string", "description": "Tenant UUID; required for relative blob filepath."},
            "filename": {"type": "string"},
            "content_type": {"type": "string"},
            "page_start": {"type": "integer"},
            "page_end": {"type": "integer"},
            "page_raw": {"type": "string"},
        },
        "required": [],
    },
    requires_confirmation=False,
)


def make_run_ocr_handler(ocr_engine_client: OcrEngineClient):
    async def handler(
        *,
        reference: str = "",
        filepath: Optional[str] = None,
        tenant_id: Optional[str] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        page_start: int = 1,
        page_end: int = 1,
        page_raw: str = "1",
        **_: Any,
    ) -> dict[str, Any]:
        pages = PageSelection(start=page_start, end=page_end, raw=page_raw)
        return await ocr_engine_client.run_ocr(
            reference,
            filepath=filepath,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            page_selection=pages,
            tenant_id=tenant_id,
        )

    return handler
