"""extract_invoice — OCR (or provided JSON) → structured invoice."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_text, norm_token

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
    '[{"description":"","qty":null,"price":null,"amount":null}]} '
    "PDF OCR often puts table headers and values on separate lines. "
    "If you see 'Invoice #' or 'Invoice No' then later a token like INV-2026-6001, "
    "that token is invoice_number. Same for 'PO #' / PO-60001 → po_number. "
    "Vendor is the seller letterhead (not Bill To). "
    "Invoice Total / Amount Due is total. "
    "If the text is only form labels (Terms, Currency, PO Number) with no values, "
    "leave every field empty. Do not guess USD or copy a label as a value."
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


@lru_cache(maxsize=256)
def _normalize_date(raw: str) -> Optional[str]:
    """Best-effort normalization to ISO YYYY-MM-DD, or None on failure.

    Code-review finding #13: there was previously no date parsing or
    validation anywhere in AP extraction — a raw OCR/LLM string (any
    format, or garbage) flowed straight into ezfb DATE-typed columns and
    the EZOFIS metadata PATCH unchecked. Deliberately month-first
    (dayfirst=False) to match this codebase's existing date fixtures/docs
    (e.g. "05/20/26" = May 20, 2026); a genuinely ambiguous or unparseable
    string is left to the caller (_as_invoice keeps the raw text and flags
    it as unparsed rather than silently substituting/guessing). Memoized
    (ultrareview efficiency fix): on the column-layout heuristic path, the
    same raw date string is parsed once by `_shape_ok` to validate it and
    again by `_as_invoice` to actually normalize it — `dateutil.parser
    .parse` isn't free, and this is a pure function of a short string, so
    caching avoids doing that work twice for no behavioral difference."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        from dateutil import parser as date_parser

        parsed = date_parser.parse(text, fuzzy=False, dayfirst=False)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.date().isoformat()


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
    if total is None:
        total = src.get("Invoice Total")
    orig_header = None
    unwrapped = _unwrap_ocr(data)
    for key in _HEADER_WRAPPERS:
        header = unwrapped.get(key)
        if isinstance(header, dict) and header:
            orig_header = header
            break
    invoice_date_raw = field_text(src, "invoice_date", "invoiceDate", "Invoice Date", "date")
    due_date_raw = field_text(src, "due_date", "dueDate", "Due Date")
    invoice_date_norm = _normalize_date(invoice_date_raw) if invoice_date_raw else None
    due_date_norm = _normalize_date(due_date_raw) if due_date_raw else None
    out: dict[str, Any] = {
        "doc_type": (src.get("doc_type") or src.get("Document Type") or "invoice"),
        "invoice_number": field_text(
            src,
            "invoice_number",
            "invoice_no",
            "invoiceNumber",
            "Invoice No",
            "Invoice #",
            "Invoice Number",
        ),
        # Normalized (YYYY-MM-DD) when the raw text parses; otherwise kept
        # as-is (never silently dropped) but flagged below so callers can
        # tell "clean ISO date" apart from "unparsed source text".
        "invoice_date": invoice_date_norm or invoice_date_raw,
        "due_date": due_date_norm or due_date_raw,
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
        "po_number": field_text(src, "po_number", "poNumber", "PO Number", "PO #", "PO No"),
        "grn_number": field_text(src, "grn_number", "grn", "GRN Number", "receipt_number"),
        "matter_id": field_text(
            src, "matter_id", "matterId", "Matter ID", "MatterId", "matter_no", "Matter No"
        ),
        "total": total,
        "currency": field_text(src, "currency", "Currency"),
        "line_items": normalized_lines,
    }
    if invoice_date_raw and not invoice_date_norm:
        out["invoice_date_unparsed"] = True
    if due_date_raw and not due_date_norm:
        out["due_date_unparsed"] = True
    header: dict[str, Any] = {}
    if orig_header:
        for key, raw in orig_header.items():
            value = field_text({str(key): raw}, str(key))
            if value:
                header[str(key)] = value
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


_INV_TOKEN = re.compile(r"\bINV[\s\-/#]*[A-Z0-9]*\d[A-Z0-9\-/]*", re.I)
_PO_TOKEN = re.compile(r"\bPO[\s\-/#]*\d[A-Z0-9\-/]*", re.I)
_MONEY_TOKEN = re.compile(r"\b\d{1,3}(?:,\d{3})+\.\d{2}\b|\b\d+\.\d{2}\b")
_TOTAL_LABEL = re.compile(
    r"(?:invoice\s*total|amount\s*due|balance\s*due|total\s*due)\s*[:\-]?\s*",
    re.I,
)
_CURRENCY_TOKEN = re.compile(r"\b(CAD|USD|EUR|GBP|INR|SGD|AED)\b", re.I)
_VENDOR_ENTITY = re.compile(r"\b(ltd|limited|inc|corp|llc|gmbh|plc|co\.?)\b", re.I)
_SKIP_VENDOR_LINE = re.compile(
    r"^(invoice|bill\s*to|ship\s*to|phone|fax|page|accounts\s*payable|"
    r"canada|united\s*states)\b",
    re.I,
)
_COLUMN_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^invoice\s*(?:#|no\.?|number)$", re.I), "Invoice No"),
    (re.compile(r"^po\s*(?:#|no\.?|number)?$", re.I), "PO Number"),
    (re.compile(r"^terms$", re.I), "Terms"),
    (re.compile(r"^ship\s*via$", re.I), "Ship Via"),
    (re.compile(r"^shipped$", re.I), "Shipped"),
    (re.compile(r"^due\s*date$", re.I), "Due Date"),
    (re.compile(r"^invoice\s*date$", re.I), "Invoice Date"),
    (re.compile(r"^currency$", re.I), "Currency"),
)


def _clean_token(match: Optional[re.Match[str]]) -> str:
    if not match:
        return ""
    return re.sub(r"\s+", "", match.group(0)).strip()


def _map_column_label(line: str) -> Optional[str]:
    text = (line or "").strip().strip(":")
    if not text:
        return None
    for pattern, label in _COLUMN_LABELS:
        if pattern.match(text):
            return label
    return None


# Code-review finding #15: per-label plausibility check for
# _header_from_column_layout's zipped (label, value) pairing, which
# otherwise assumes the label block and value block line up 1:1 with no
# OCR-dropped/reordered line in either — a single missed/extra line
# silently shifts every subsequent value onto the wrong label (e.g. a PO
# number landing under "Invoice No"). Labels not listed here (Terms, Ship
# Via, Shipped) have no strong expected shape and are left unchecked.
def _shape_ok(label: str, value: str) -> bool:
    if label in ("Invoice No", "PO Number"):
        return any(ch.isdigit() for ch in value)
    if label in ("Due Date", "Invoice Date"):
        return _normalize_date(value) is not None
    if label == "Currency":
        return bool(_CURRENCY_TOKEN.fullmatch(value.strip()))
    return True


def _header_from_column_layout(text: str) -> dict[str, Any]:
    """Map a block of header labels followed by the same number of value lines.

    OCR of invoice tables often yields:
    Invoice # / PO # / Terms / ... then INV-2026-6001 / PO-60001 / ...
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    header: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        labels: list[str] = []
        cursor = index
        while cursor < len(lines):
            mapped = _map_column_label(lines[cursor])
            if mapped is None:
                break
            labels.append(mapped)
            cursor += 1
        if len(labels) >= 2 and cursor + len(labels) <= len(lines):
            values = lines[cursor : cursor + len(labels)]
            if not any(_map_column_label(value) for value in values):
                for label, value in zip(labels, values):
                    token = field_text({label: value}, label)
                    if not token:
                        continue
                    if not _shape_ok(label, token):
                        logger.warning(
                            "ap_extract_column_layout_shape_mismatch",
                            extra={"label": label, "value": token[:50]},
                        )
                        continue
                    header[label] = token
                index = cursor + len(labels)
                continue
        index += 1
    return header


# Code-review finding #14: how many lines of a "Bill To"/"Ship To" block
# to keep skipping after the header line, as a safety cap in case a
# malformed/unusual layout never hits a blank line or a recognized column
# label to end the block naturally.
_BUYER_BLOCK_MAX_LINES = 5
_BUYER_BLOCK_START = re.compile(r"^(bill\s*to|ship\s*to)\b", re.I)


def _guess_vendor(text: str) -> str:
    """First entity-suffixed line NOT inside a "Bill To"/"Ship To" block.

    Previously only the label line itself ("Bill To:") was skipped — the
    buyer's company name on the following line(s) (the actual address
    block) was not, so it could win as the "vendor" if it also happened to
    contain an entity suffix (Ltd/Inc/Corp/...), swapping buyer and seller.
    Now the whole block is skipped until a blank line, a recognized column
    label, or the line-count cap ends it."""
    in_buyer_block = False
    buyer_block_lines = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            in_buyer_block = False
            continue
        if _BUYER_BLOCK_START.match(line):
            in_buyer_block = True
            buyer_block_lines = 0
            continue
        if in_buyer_block:
            buyer_block_lines += 1
            if buyer_block_lines > _BUYER_BLOCK_MAX_LINES or _map_column_label(line):
                in_buyer_block = False
            else:
                continue
        if len(line) < 4 or _SKIP_VENDOR_LINE.match(line) or "@" in line:
            continue
        if _VENDOR_ENTITY.search(line):
            return line
    return ""


def _guess_total(text: str) -> Any:
    match = _TOTAL_LABEL.search(text or "")
    if match:
        money = _MONEY_TOKEN.search(text[match.end() :])
        if money:
            return money.group(0)
    return None


def _heuristic_from_text(text: str) -> dict[str, Any]:
    column = _header_from_column_layout(text)
    labeled = _header_from_labeled_text(text)
    merged = {**labeled, **column}
    invoice_number = field_text(
        merged, "Invoice No", "Invoice #", "Invoice Number", "invoice_number"
    ) or _clean_token(_INV_TOKEN.search(text or ""))
    po_number = field_text(merged, "PO Number", "PO #", "po_number") or _clean_token(
        _PO_TOKEN.search(text or "")
    )
    vendor = field_text(merged, "Vendor Name", "Supplier", "vendor") or _guess_vendor(text)
    total = merged.get("Invoice Amount") or merged.get("Invoice Total") or _guess_total(text)
    currency = field_text(merged, "Currency", "currency")
    if not currency:
        found = _CURRENCY_TOKEN.search(text or "")
        if found:
            currency = found.group(1).upper()
    due_date = field_text(merged, "Due Date", "due_date")
    invoice_date = field_text(merged, "Invoice Date", "invoice_date", "Shipped")
    payload = {
        "doc_type": "invoice" if re.search(r"\binvoice\b", text or "", re.I) else "other",
        "invoice_number": invoice_number,
        "po_number": po_number,
        "vendor": vendor,
        "total": total,
        "currency": currency,
        "due_date": due_date,
        "invoice_date": invoice_date,
        "invoice_header": merged,
    }
    return _as_invoice(payload)


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


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


# Top-level fields gated by _is_grounded — plain text/number values that
# should plausibly trace back to the source OCR text. `doc_type` (a
# classification, not an extracted value) and `line_items` (structural,
# not meaningfully "grounded" the same way) are deliberately excluded.
_GROUNDABLE_FIELDS = frozenset(
    {
        "invoice_number",
        "po_number",
        "vendor",
        "total",
        "currency",
        "due_date",
        "invoice_date",
        "grn_number",
        "matter_id",
    }
)


def _ocr_grounding_haystacks(ocr_text: str) -> tuple[str, str]:
    """(normalized alnum text, digit-only text) for the WHOLE OCR document
    — computed once per _coalesce_invoice call and reused across every
    groundable field it checks (ultrareview efficiency fix: this used to
    be recomputed by re-normalizing the full OCR text on every single
    field check — up to 9x O(len(ocr_text)) work per extraction — instead
    of once)."""
    text = ocr_text or ""
    return norm_token(text), "".join(ch for ch in text if ch.isdigit())


def _is_grounded_in(value: Any, *, norm_text: str, digit_text: str) -> bool:
    if not norm_text or value is None:
        return False
    if isinstance(value, (int, float)):
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) < 3:
            # Too short to mean anything against a substring search —
            # treat as ungrounded rather than risk a trivial false accept.
            return False
        return digits in digit_text
    if not isinstance(value, str):
        return True
    norm_value = norm_token(value)
    if not norm_value:
        return False
    return norm_value in norm_text


def _is_grounded(value: Any, ocr_text: str) -> bool:
    """Best-effort check that `value` plausibly traces back to the OCR
    source text — a defense against LLM hallucination (code-review finding
    #6). The extraction prompt already instructs the model not to invent
    values, but nothing previously verified that server-side; a fabricated
    invoice number/vendor/total was accepted purely because it was
    non-empty. Deliberately lenient (normalized substring match, not exact)
    to tolerate OCR/LLM formatting differences — this catches clear
    fabrication, not minor rendering variance. Single-value convenience
    wrapper around `_is_grounded_in`; `_coalesce_invoice` calls
    `_is_grounded_in` directly with pre-computed haystacks instead, since
    it checks many values against the same OCR text."""
    norm_text, digit_text = _ocr_grounding_haystacks(ocr_text)
    return _is_grounded_in(value, norm_text=norm_text, digit_text=digit_text)


def _coalesce_invoice(
    primary: Optional[dict[str, Any]], fallback: dict[str, Any], ocr_text: str = ""
) -> tuple[dict[str, Any], list[str]]:
    """Keep LLM values when present AND grounded in the OCR text; fill
    blanks (or reject ungrounded LLM values) from OCR heuristics instead.
    Returns (invoice, ungrounded_field_names) — the caller logs/records
    the rejected fields; they never appear inside the invoice dict itself
    (which flows straight into ezfb/EZOFIS metadata writes)."""
    if not primary:
        return fallback, []
    merged = dict(fallback)
    ungrounded: list[str] = []
    norm_text, digit_text = _ocr_grounding_haystacks(ocr_text)
    for key, value in primary.items():
        if key == "invoice_header":
            continue
        if not _filled(value):
            continue
        if key in _GROUNDABLE_FIELDS and not _is_grounded_in(value, norm_text=norm_text, digit_text=digit_text):
            ungrounded.append(key)
            continue
        merged[key] = value
    header = {
        **(fallback.get("invoice_header") or {}),
        **(primary.get("invoice_header") or {}),
    }
    if header:
        merged["invoice_header"] = header
    return _as_invoice(merged), ungrounded


async def _structure_with_llm(
    ctx: ApContext, ocr_text: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Returns (invoice_or_None, usage_or_None). `usage` is populated
    whenever the LLM call itself succeeded — even if the response content
    couldn't be parsed into an invoice — so token spend is never silently
    dropped (code-review finding #5: this call's usage previously wasn't
    captured anywhere, and EzofisClient.charge_activity_credit hardcoded
    inputTokens/outputTokens/totalTokens to 0 regardless)."""
    if ctx.llm is None or not ocr_text.strip():
        return None, None
    usage: Optional[dict[str, Any]] = None
    try:
        result = await ctx.llm.chat_completion(
            [
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": ocr_text[:12000]},
            ],
            **(ctx.llm_overrides or {}),
        )
        usage = (result or {}).get("usage")
        content = (result or {}).get("content") or ""
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None, usage
        parsed = json.loads(content[start : end + 1])
        if isinstance(parsed, dict):
            return _as_invoice(parsed), usage
    except Exception:
        logger.warning("ap_extract_llm_failed", extra={"error_type": "llm"})
    return None, usage


def _completeness(invoice: dict[str, Any]) -> dict[str, Any]:
    """How much of a usable invoice extraction actually found — read by
    ApSkillRunner.run to decide whether a run should report "completed" or
    "completed_low_confidence" (code-review finding #3: a near-empty
    extraction was previously reported identically to a clean one)."""
    has_invoice_number = bool(field_text(invoice, "invoice_number"))
    has_vendor = bool(field_text(invoice, "vendor"))
    has_po_number = bool(field_text(invoice, "po_number"))
    has_total = invoice.get("total") is not None
    return {
        "has_invoice_number": has_invoice_number,
        "has_vendor": has_vendor,
        "has_po_number": has_po_number,
        "has_total": has_total,
        "fields_found": sum((has_invoice_number, has_vendor, has_po_number, has_total)),
        "fields_checked": 4,
    }


async def run(ctx: ApContext) -> ApSkillResult:
    provided = ctx.invoice_json or (ctx.document_job.get("invoice_json") if ctx.document_job else None)
    if isinstance(provided, dict) and provided:
        invoice = _as_invoice(provided)
        ctx.invoice_json = invoice
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "invoice": invoice,
                "source": "invoice_json",
                "ocr_text": "",
                "data_quality": _completeness(invoice),
                "usage": None,
            },
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
    except Exception as exc:
        # Code-review finding #18: ToolExecutionError/OcrEngineError are
        # both already subclasses of Exception — listing them alongside it
        # was a redundant no-op that just obscured which failures are
        # "expected" (an OCR/tool integration error) vs. genuinely
        # unexpected (a bug). Both still land here either way; the intent
        # (OCR failure -> ApSkillError, never crash the whole run) is
        # unchanged.
        logger.warning("ap_extract_ocr_failed", extra={"error_type": type(exc).__name__})
        raise ApSkillError("OCR extraction failed for this document.") from exc

    ocr_text = (ocr_tool.get("text") or "").strip() if isinstance(ocr_tool, dict) else ""
    heuristic = _heuristic_from_text(ocr_text) if ocr_text else _as_invoice({})
    llm_invoice, llm_usage = await _structure_with_llm(ctx, ocr_text)
    invoice, ungrounded_fields = _coalesce_invoice(llm_invoice, heuristic, ocr_text)
    if ungrounded_fields:
        logger.warning(
            "ap_extract_llm_ungrounded_field",
            extra={"fields": ungrounded_fields},
        )
    if not (
        invoice.get("invoice_number")
        or invoice.get("po_number")
        or invoice.get("vendor")
        or invoice.get("total") is not None
    ):
        invoice["currency"] = ""
        header = {
            key: value
            for key, value in (invoice.get("invoice_header") or {}).items()
            if field_text({str(key): value}, str(key))
        }
        if header:
            invoice["invoice_header"] = header
        else:
            invoice.pop("invoice_header", None)
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
            "data_quality": _completeness(invoice),
            "usage": llm_usage,
            "llm_ungrounded_fields": ungrounded_fields,
        },
    )
