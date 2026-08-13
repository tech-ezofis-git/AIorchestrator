"""extract_invoice — OCR (or provided JSON) → structured invoice."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_text
from app.core.dispatcher import ToolExecutionError
from app.integrations.ocr_engine import OcrEngineError

logger = logging.getLogger("orchestrator.ap.extract_invoice")

SKILL_ID = "extract_invoice"

_EXTRACT_PROMPT = (
    "Extract AP invoice fields from the OCR text. Reply with JSON only, no markdown: "
    '{"doc_type":"invoice"|"other","invoice_number":"","invoice_date":"","due_date":"",'
    '"vendor":"","po_number":"","total":null,"currency":"","line_items":'
    '[{"description":"","qty":null,"price":null,"amount":null}]}'
)


def _as_invoice(data: dict[str, Any]) -> dict[str, Any]:
    lines = data.get("line_items") or data.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    normalized_lines = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        normalized_lines.append(
            {
                "description": field_text(line, "description", "item", "name"),
                "qty": line.get("qty") if line.get("qty") is not None else line.get("quantity"),
                "price": line.get("price") if line.get("price") is not None else line.get("unit_price"),
                "amount": line.get("amount") if line.get("amount") is not None else line.get("line_amount"),
            }
        )
    return {
        "doc_type": (data.get("doc_type") or "invoice"),
        "invoice_number": field_text(
            data, "invoice_number", "invoice_no", "invoiceNumber", "Invoice No"
        ),
        "invoice_date": field_text(data, "invoice_date", "invoiceDate", "date"),
        "due_date": field_text(data, "due_date", "dueDate", "Due Date"),
        "vendor": field_text(
            data, "vendor", "supplier", "vendor_name", "supplier_name", "Vendor Name"
        ),
        "po_number": field_text(data, "po_number", "poNumber", "po", "PO Number"),
        "grn_number": field_text(data, "grn_number", "grn", "GRN Number", "receipt_number"),
        "matter_id": field_text(
            data, "matter_id", "matterId", "Matter ID", "MatterId", "matter_no", "Matter No"
        ),
        "total": data.get("total") if data.get("total") is not None else data.get("amount"),
        "currency": field_text(data, "currency") or "USD",
        "line_items": normalized_lines,
    }


def _heuristic_from_text(text: str) -> dict[str, Any]:
    def _search(*patterns: str) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    return _as_invoice(
        {
            "doc_type": "invoice" if re.search(r"\binvoice\b", text, re.I) else "other",
            "invoice_number": _search(
                r"invoice\s*(?:no|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,})",
            ),
            "due_date": _search(r"due\s*date\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"),
            "po_number": _search(r"\bpo(?:\s*number|\s*#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{1,})"),
            "vendor": _search(r"(?:vendor|supplier)\s*[:\-]?\s*([A-Za-z0-9 .,&-]{3,80})"),
        }
    )


async def _structure_with_llm(ctx: ApContext, ocr_text: str) -> Optional[dict[str, Any]]:
    if ctx.llm is None or not ocr_text.strip():
        return None
    try:
        result = await ctx.llm.chat_completion(
            [
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": ocr_text[:12000]},
            ]
        )
        content = (result or {}).get("content") or ""
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        parsed = json.loads(content[start : end + 1])
        if isinstance(parsed, dict):
            return _as_invoice(parsed)
    except Exception:
        logger.warning("ap_extract_llm_failed", extra={"error_type": "llm"})
    return None


async def run(ctx: ApContext) -> ApSkillResult:
    provided = ctx.invoice_json or (ctx.document_job.get("invoice_json") if ctx.document_job else None)
    if isinstance(provided, dict) and provided:
        invoice = _as_invoice(provided)
        ctx.invoice_json = invoice
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={"invoice": invoice, "source": "invoice_json", "ocr_text": ""},
        )

    job = ctx.document_job or {}
    file_bytes = job.get("file_bytes")
    filepath = (job.get("filepath") or "").strip() or None
    if file_bytes is None and not filepath:
        raise ApSkillError(
            "extract_invoice requires invoice_json, an uploaded file, or payload.filepath."
        )
    if ctx.dispatcher is None:
        raise ApSkillError("extract_invoice is not wired to the OCR dispatcher.")

    settings = ctx.settings
    try:
        pages = resolve_pageno(job.get("pageno"), max_pages=getattr(settings, "ocr_max_pages", 5))
    except InvalidOcrPageError as exc:
        raise ApSkillError(str(exc)) from exc

    source = filepath or job.get("filename") or "upload"
    try:
        ocr_tool = await ctx.dispatcher.dispatch(
            "run_ocr",
            {
                "reference": source,
                "filepath": filepath,
                "filename": job.get("filename"),
                "content_type": job.get("content_type"),
                "file_bytes": file_bytes,
                "page_start": pages.start,
                "page_end": pages.end,
                "page_raw": pages.raw,
            },
        )
    except (ToolExecutionError, OcrEngineError, Exception) as exc:
        logger.warning("ap_extract_ocr_failed", extra={"error_type": type(exc).__name__})
        raise ApSkillError("OCR extraction failed for this document.") from exc

    ocr_text = (ocr_tool.get("text") or "").strip() if isinstance(ocr_tool, dict) else ""
    invoice = await _structure_with_llm(ctx, ocr_text) or _heuristic_from_text(ocr_text)
    if not invoice.get("invoice_number") and not ocr_text:
        raise ApSkillError("OCR returned no text and no invoice_json was provided.")
    ctx.invoice_json = invoice
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "invoice": invoice,
            "source": source,
            "ocr_text": ocr_text,
            "ocr_mock": bool(ocr_tool.get("mock")) if isinstance(ocr_tool, dict) else False,
        },
    )
