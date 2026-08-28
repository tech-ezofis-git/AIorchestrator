"""Rules and normalization helpers for the PDF generation skill."""
from __future__ import annotations

import ast
import html
import json
import re
from datetime import datetime
from typing import Any, Optional


_THEMES: dict[str, dict[str, str]] = {
    "corporate_blue": {
        "primary": "#1E40AF",      # Deep blue
        "secondary": "#3B82F6",    # Vibrant blue
        "accent": "#60A5FA",       # Light accent
        "text": "#0F172A",         # Slate 900
        "text_muted": "#64748B",   # Slate 500
        "bg_card": "#F8FAFC",      # Slate 50
        "border": "#E2E8F0",       # Slate 200
        "table_head_bg": "#1E40AF",
        "table_head_text": "#FFFFFF",
        "table_alt_bg": "#F1F5F9",
    },
    "emerald": {
        "primary": "#065F46",      # Emerald 800
        "secondary": "#059669",    # Emerald 600
        "accent": "#34D399",       # Emerald 400
        "text": "#064E3B",         # Deep green
        "text_muted": "#6B7280",
        "bg_card": "#F0FDF4",      # Light emerald tint
        "border": "#D1FAE5",
        "table_head_bg": "#065F46",
        "table_head_text": "#FFFFFF",
        "table_alt_bg": "#F0FDF4",
    },
    "graphite": {
        "primary": "#18181B",      # Zinc 900
        "secondary": "#52525B",    # Zinc 600
        "accent": "#A1A1AA",       # Zinc 400
        "text": "#18181B",
        "text_muted": "#71717A",
        "bg_card": "#FAFAFA",
        "border": "#E4E4E7",
        "table_head_bg": "#18181B",
        "table_head_text": "#FFFFFF",
        "table_alt_bg": "#F4F4F5",
    },
    "purple": {
        "primary": "#581C87",      # Purple 900
        "secondary": "#7E22CE",    # Purple 700
        "accent": "#C084FC",       # Purple 400
        "text": "#3B0764",
        "text_muted": "#6B7280",
        "bg_card": "#FAF5FF",
        "border": "#E9D5FF",
        "table_head_bg": "#581C87",
        "table_head_text": "#FFFFFF",
        "table_alt_bg": "#F5F3FF",
    },
    "amber": {
        "primary": "#78350F",      # Amber 900
        "secondary": "#D97706",    # Amber 600
        "accent": "#FBBF24",       # Amber 400
        "text": "#451A03",
        "text_muted": "#78716C",
        "bg_card": "#FFFBEB",
        "border": "#FEF3C7",
        "table_head_bg": "#78350F",
        "table_head_text": "#FFFFFF",
        "table_alt_bg": "#FEF3C7",
    },
}

DEFAULT_THEME = "corporate_blue"


def resolve_theme(theme_name: Optional[str]) -> dict[str, str]:
    """Returns color dictionary for theme name with corporate_blue fallback."""
    if not theme_name:
        return _THEMES[DEFAULT_THEME]
    normalized = theme_name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _THEMES:
        return _THEMES[normalized]
    # Check partial match
    for k, v in _THEMES.items():
        if k in normalized or normalized in k:
            return v
    return _THEMES[DEFAULT_THEME]


def infer_document_title(data: dict[str, Any], explicit_title: Optional[str] = None) -> str:
    """Infers an appropriate document title from explicit string or common JSON fields."""
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()

    candidate_keys = [
        "title",
        "document_title",
        "documentTitle",
        "report_title",
        "reportTitle",
        "invoice_title",
        "name",
        "document_name",
        "workflowName",
        "workflow_name",
        "subject",
        "header",
    ]
    for key in candidate_keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Check for invoice-like keys
    if "invoice_number" in data or "invoice_no" in data or "invoiceNo" in data:
        inv_num = data.get("invoice_number") or data.get("invoice_no") or data.get("invoiceNo")
        return f"Invoice {inv_num}"

    if "purchase_order_number" in data or "po_number" in data or "poNumber" in data:
        po_num = data.get("purchase_order_number") or data.get("po_number") or data.get("poNumber")
        return f"Purchase Order {po_num}"

    return "Document Summary Report"


def sanitize_filename(title: str, suffix: str = ".pdf") -> str:
    """Sanitizes document title into a safe filename."""
    cleaned = re.sub(r"[^\w\s\-.]", "", title).strip()
    cleaned = re.sub(r"[\s]+", "_", cleaned)
    if not cleaned:
        cleaned = "document"
    if not cleaned.lower().endswith(suffix.lower()):
        cleaned += suffix
    return cleaned


def clean_html_text(text: str) -> str:
    """Strips HTML tags and unescapes entities."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return html.unescape(cleaned).strip()


def format_cell_value(val: Any) -> str:
    """Formats arbitrary or Ezofis-specific JSON values for PDF table or text display."""
    if val is None:
        return "-"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)):
        if isinstance(val, float):
            if val.is_integer():
                return f"{int(val)}"
            return f"{val:,.2f}"
        return f"{val:,}"
    if isinstance(val, list):
        if not val:
            return "-"
        if all(isinstance(x, (str, int, float, bool)) for x in val):
            return ", ".join(clean_html_text(str(x)) for x in val)
        return f"{len(val)} items"
    if isinstance(val, dict):
        # Ezofis CURRENCY_AMOUNT: {"currency": "INR", "value": "1500"}
        if "currency" in val and "value" in val:
            c = str(val.get("currency") or "").strip()
            v = str(val.get("value") or "").strip()
            return f"{c} {v}".strip() if (c or v) else "-"
        # Ezofis PHONE_NUMBER: {"code": "91", "phoneNo": "...", "verified": True}
        if "phoneNo" in val or "phone_no" in val:
            code = str(val.get("code") or "").strip()
            num = str(val.get("phoneNo") or val.get("phone_no") or "").strip()
            status = " (Verified)" if val.get("verified") is True else ""
            prefix = f"+{code} " if code else ""
            return f"{prefix}{num}{status}".strip() if (code or num) else "-"
        # Generic sub-dict into key: value pairs
        parts = []
        for k, v in val.items():
            if isinstance(v, (str, int, float, bool)):
                parts.append(f"{k}: {format_cell_value(v)}")
        return "; ".join(parts) if parts else "[Object]"

    # String value
    raw_str = str(val).strip()
    if not raw_str:
        return "-"

    # Check if string is a stringified JSON object/list
    if (raw_str.startswith("{") and raw_str.endswith("}")) or (raw_str.startswith("[") and raw_str.endswith("]")):
        try:
            parsed = json.loads(raw_str)
            return format_cell_value(parsed)
        except Exception:
            try:
                parsed = ast.literal_eval(raw_str)
                return format_cell_value(parsed)
            except Exception:
                pass

    # Check for ISO Date pattern YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_str):
        try:
            dt = datetime.strptime(raw_str, "%Y-%m-%d")
            return dt.strftime("%d-%m-%Y")
        except Exception:
            pass

    return clean_html_text(raw_str)


get_theme = resolve_theme
infer_title = infer_document_title
