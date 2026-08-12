"""run_ocr tool — ToolSchema-conformant wrapper around
OcrEngineClient.run_ocr (mocked; see app/integrations/ocr_engine.py — no
real OCR engine is chosen yet). Registered with the Dispatcher in
app/main.py's lifespan and called only through it — agents never call the
engine client directly.
"""
from typing import Any

from app.integrations.ocr_engine import OcrEngineClient
from app.models.tool_schema import ToolSchema

RUN_OCR_SCHEMA = ToolSchema(
    name="run_ocr",
    description="Extract text from a document/image reference via OCR.",
    parameters={
        "type": "object",
        "properties": {
            "reference": {"type": "string", "description": "The document/image reference to run OCR on."},
        },
        "required": ["reference"],
    },
    requires_confirmation=False,
)


def make_run_ocr_handler(ocr_engine_client: OcrEngineClient):
    """Returns an async handler closing over `ocr_engine_client`, matching
    the Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, reference: str) -> dict[str, Any]:
        return await ocr_engine_client.run_ocr(reference)

    return handler
