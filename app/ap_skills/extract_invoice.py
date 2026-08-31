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

_HEADER_WRAPPERS = (
    "invoice_header",
    "invoiceHeader",
    "header",
    "po_row",
    "Extracted Invoice JSON",
)
_LINE_KEYS = (
    "Invoice Extracted Line Item",
    "Line Item",
    "Line Items",
    "line_items",
    "lines",
)

_EXTRACT_PROMPT = (
    "Extract AP invoice fields from the OCR text. Reply with JSON only, no markdown: "
    '{"doc_type":"invoice"|"other","invoice_number":"","invoice_date":"","due_date":"",'
    '"vendor":"","po_number":"","total":null,"currency":"","line_items":'
    '[{"description":"","qty":null,"price":null,"amount":null}]}'
)


def _unwrap_ocr(data: dict[str, Any]) -> dict[str, Any]:
    """Strip GetOCRJSON wrappers (`output`, `invoice`) used by apagentv6."""
    if not isinstance(data, dict):
        return {}
    for key in ("output", "invoice"):
        inner = data.get(key)
        if not isinstance(inner, dict) or not inner:
            continue
        if inner.get("invoice_header") or inner.get("line_items") or inner.get("Line Item"):
            return inner
    return data


def _merged_header_source(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested invoice_header labels onto a single lookup dict."""
    data = _unwrap_ocr(data)
    merged: dict[str, Any] = {}
    for key in _HEADER_WRAPPERS:
        header = data.get(key)
        if isinstance(header, dict) and header:
            merged.update(header)
            break
    for key, value in data.items():
        if key in _HEADER_WRAPPERS or key in _LINE_KEYS:
            continue
        merged[key] = value
    return merged


def _raw_line_items(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    data = _unwrap_ocr(data)
    for key in _LINE_KEYS:
        lines = data.get(key)
        if isinstance(lines, list) and lines:
            return key, [row for row in lines if isinstance(row, dict)]
    return "line_items", []


def _as_invoice(data: dict[str, Any]) -> dict[str, Any]:
    src = _merged_header_source(data)
    orig_key, raw_lines = _raw_line_items(data)
    normalized_lines = []
    for line in raw_lines:
        qty = line.get("qty")
        if qty is None:
            qty = line.get("quantity")
        if qty is None:
            qty = line.get("Quantity")
        price = line.get("price")
        if price is None:
            price = line.get("unit_price")
        if price is None:
            price = line.get("rate")
        amount = line.get("amount")
        if amount is None:
            amount = line.get("line_amount")
        if amount is None:
            amount = line.get("Extended")
        normalized_lines.append(
            {
                "description": field_text(line, "description", "item", "name", "Description"),
                "qty": qty,
                "price": price,
                "amount": amount,
            }
        )
    total = src.get("total")
    if total is None:
        total = src.get("amount")
    if total is None:
        total = src.get("Invoice Amount")
    if total is None:
        total = src.get("invoice_amount")
    orig_header = None
    unwrapped = _unwrap_ocr(data)
    for key in _HEADER_WRAPPERS:
        header = unwrapped.get(key)
        if isinstance(header, dict) and header:
            orig_header = header
            break
    out: dict[str, Any] = {
        "doc_type": (src.get("doc_type") or src.get("Document Type") or "invoice"),
        "invoice_number": field_text(
            src, "invoice_number", "invoice_no", "invoiceNumber", "Invoice No"
        ),
        "invoice_date": field_text(src, "invoice_date", "invoiceDate", "Invoice Date", "date"),
        "due_date": field_text(src, "due_date", "dueDate", "Due Date"),
        "vendor": field_text(
            src,
            "vendor",
            "supplier",
            "vendor_name",
            "supplier_name",
            "Vendor Name",
            "VENDOR Name",
            "Supplier",
            "Supplier Name",
        ),
        "po_number": field_text(src, "po_number", "poNumber", "po", "PO Number"),
        "grn_number": field_text(src, "grn_number", "grn", "GRN Number", "receipt_number"),
        "matter_id": field_text(
            src, "matter_id", "matterId", "Matter ID", "MatterId", "matter_no", "Matter No"
        ),
        "total": total,
        "currency": field_text(src, "currency", "Currency"),
        "line_items": normalized_lines,
    }
    if not out["currency"] and (
        out["invoice_number"] or out["po_number"] or out["vendor"] or total is not None
    ):
        out["currency"] = "USD"
    header = dict(orig_header) if orig_header else {}
    if out["invoice_number"]:
        header.setdefault("Invoice No", out["invoice_number"])
    if out["po_number"]:
        header.setdefault("PO Number", out["po_number"])
    if out["vendor"]:
        header.setdefault("Vendor Name", out["vendor"])
        header.setdefault("Supplier", out["vendor"])
    if out["invoice_date"]:
        header.setdefault("Invoice Date", out["invoice_date"])
    if out["due_date"]:
        header.setdefault("Due Date", out["due_date"])
    if out["currency"]:
        header.setdefault("Currency", out["currency"])
    if total is not None:
        header.setdefault("Invoice Amount", str(total))
    if header:
        out["invoice_header"] = header
    if raw_lines and orig_key != "line_items":
        out[orig_key] = raw_lines
    return out


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


def _header_from_labeled_text(text: str) -> dict[str, Any]:
    """Parse 'Invoice No: INV-1' lines; ignore label-only values like Terms: Terms."""
    header: dict[str, Any] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        key_norm = "".join(ch for ch in key.lower() if ch.isalnum())
        val_norm = "".join(ch for ch in value.lower() if ch.isalnum())
        if key_norm == val_norm:
            continue
        header[key] = value
    return header


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
                "tenant_id": job.get("tenant_id") or ctx.tenant_id,
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
    labeled = _header_from_labeled_text(ocr_text)
    invoice = await _structure_with_llm(ctx, ocr_text) or _heuristic_from_text(ocr_text)
    if labeled:
        merged_header = {**(invoice.get("invoice_header") or {}), **labeled}
        invoice = _as_invoice({**invoice, "invoice_header": merged_header})
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
