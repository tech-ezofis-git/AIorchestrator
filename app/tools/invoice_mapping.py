"""Invoice field mapping shared by the FTP agent and the File Preparation tool."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.classification_skills.contract import normalize_document_type
from app.tools.invoice_scorer import is_null

FTP_AGENT_NAME = "FTP"

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "invoiceNumber": ("invoice No", "Invoice No", "invoice_number", "Invoice Number", "invoiceNumber"),
    "invoiceDate": ("invoice Date", "Invoice Date", "invoice_date", "invoiceDate"),
    "vendorName": ("Company Name", "supplier_name", "Vendor Name", "vendorName"),
    "currency": ("Currency", "currency"),
    "totalAmount": (
        "Grand Total",
        "Invoice Total (net)",
        "Invoice Total",
        "Total Amount",
        "total_due",
        "totalAmount",
    ),
}
_LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "description": ("Item description", "Item Description", "description", "Description"),
    "quantity": ("quantity", "Quantity", "Qty"),
    "unitPrice": ("Rate", "Unit Price", "unitPrice", "unit_price"),
    "lineAmount": ("Line Amount", "lineAmount", "Amount", "line_amount"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in row and not is_null(row[key]):
            return row[key]
    return None


def to_number(value: Any) -> Optional[float]:
    if is_null(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -abs(number) if negative else number


def _to_int_if_whole(value: Any) -> Any:
    number = to_number(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _to_iso_date(value: Any) -> Optional[str]:
    if is_null(value):
        return None
    text = str(value).strip()
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(text, dayfirst=False).date().isoformat()
    except (ValueError, OverflowError, ImportError):
        return text


def map_invoice_header(header: Any) -> dict[str, Any]:
    row = header if isinstance(header, dict) else {}
    return {
        "invoiceNumber": _pick(row, _HEADER_ALIASES["invoiceNumber"]),
        "invoiceDate": _to_iso_date(_pick(row, _HEADER_ALIASES["invoiceDate"])),
        "vendorName": _pick(row, _HEADER_ALIASES["vendorName"]),
        "currency": _pick(row, _HEADER_ALIASES["currency"]),
        "totalAmount": to_number(_pick(row, _HEADER_ALIASES["totalAmount"])),
    }


def map_line_items(items: Any) -> list[dict[str, Any]]:
    rows = items if isinstance(items, list) else []
    mapped: list[dict[str, Any]] = []
    for index, item in enumerate(r for r in rows if isinstance(r, dict)):
        mapped.append(
            {
                "lineNo": _to_int_if_whole(item.get("lineNo")) or index + 1,
                "description": _pick(item, _LINE_ALIASES["description"]),
                "quantity": _to_int_if_whole(_pick(item, _LINE_ALIASES["quantity"])),
                "unitPrice": to_number(_pick(item, _LINE_ALIASES["unitPrice"])),
                "lineAmount": to_number(_pick(item, _LINE_ALIASES["lineAmount"])),
            }
        )
    return mapped


def extraction_confidence(ocr_json: dict[str, Any], ratio: float) -> float:
    """`confidence` from the JSON (0–1 or 0–100), else 1 - null ratio."""
    raw = ocr_json.get("confidence")
    if raw is None and isinstance(ocr_json.get("invoice_header"), dict):
        raw = ocr_json["invoice_header"].get("confidence")
    number = to_number(raw)
    if number is None:
        return round(1.0 - ratio, 2)
    if number > 1.0:
        number = number / 100.0
    return round(max(0.0, min(1.0, number)), 2)


def remark(remarks: Optional[dict[str, Any]], *names: str) -> Optional[str]:
    """Case/spacing-insensitive lookup: 'unique ref no' == 'Unique_Ref_No'."""
    if not isinstance(remarks, dict):
        return None
    wanted = {re.sub(r"[\s_\-]+", "", n).lower() for n in names}
    for key, value in remarks.items():
        if re.sub(r"[\s_\-]+", "", str(key)).lower() in wanted and not is_null(value):
            return str(value).strip()
    return None


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]+", "_", value).strip("._") or "document"


def document_type(job: dict[str, Any], header: Any) -> str:
    explicit = normalize_document_type(job.get("document_type"))
    if explicit != "UNKNOWN":
        return explicit
    if isinstance(header, dict):
        return normalize_document_type(header.get("Document Type"))
    return "UNKNOWN"
