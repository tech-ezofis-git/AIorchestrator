"""Invoice Scorer tool — invoice extraction JSON → share of null fields → valid / not valid.

Not valid when more than `ftp_unprocessed_null_ratio` (default 0.80) of the
header + line-item values are null or empty.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import Settings, get_settings

TOOL_ID = "invoice_scorer"
NO_JSON_ERROR = "Invoice extraction JSON is required."
_PLACEHOLDER_KEYS = {"..."}


def is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none"})


def invoice_parts(data: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[Any]]:
    """(header, line_items) from raw LLM output, a Ramco/FTP contract, or its `extraction` block."""
    data = data if isinstance(data, dict) else {}
    if isinstance(data.get("extraction"), dict):
        data = data["extraction"]
    header = data.get("invoice_header", data.get("invoiceHeader"))
    items = data.get("line_items", data.get("lineItems"))
    return (header if isinstance(header, dict) else {}), (items if isinstance(items, list) else [])


def _fields(header: dict[str, Any], items: list[Any]) -> list[tuple[str, Any]]:
    fields = [(f"invoice_header.{k}", v) for k, v in header.items() if k not in _PLACEHOLDER_KEYS]
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            fields.extend(
                (f"line_items[{index}].{k}", v)
                for k, v in item.items()
                if k != "lineNo" and k not in _PLACEHOLDER_KEYS
            )
    return fields


def score_invoice(
    ocr_json: Optional[dict[str, Any]],
    *,
    threshold: Optional[float] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Score the extraction. Never raises; no fields at all counts as 100% null (not valid)."""
    limit = threshold if threshold is not None else (settings or get_settings()).ftp_unprocessed_null_ratio
    if not isinstance(ocr_json, dict) or not ocr_json:
        return {
            "tool": TOOL_ID,
            "status": "FAILED",
            "valid": False,
            "decision": "NOT_VALID",
            "null_ratio": 1.0,
            "null_percent": 100.0,
            "threshold_percent": round(limit * 100, 2),
            "total_fields": 0,
            "null_count": 0,
            "null_fields": [],
            "error": NO_JSON_ERROR,
        }

    header, items = invoice_parts(ocr_json)
    fields = _fields(header, items)
    null_fields = [name for name, value in fields if is_null(value)]
    ratio = len(null_fields) / len(fields) if fields else 1.0
    valid = ratio <= limit
    return {
        "tool": TOOL_ID,
        "status": "SUCCEEDED",
        "valid": valid,
        "decision": "VALID" if valid else "NOT_VALID",
        "null_ratio": round(ratio, 4),
        "null_percent": round(ratio * 100, 2),
        "threshold_percent": round(limit * 100, 2),
        "total_fields": len(fields),
        "null_count": len(null_fields),
        "null_fields": null_fields,
        "error": None,
    }
