"""Fill null OCR fields from labels that are actually in the OCR text.

The model often returns null for AP names such as InvoiceNo when the page
says "Invoice Number". Only a value copied from the text is written back.
"""
from __future__ import annotations

import re
from typing import Any

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# Compact field name → label keys, most specific first.
_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "invoiceno": ("invoicenumber", "invoiceno", "invoice"),
    "invoicedate": ("invoicedate", "documentdate", "date"),
    "duedate": ("duedate",),
    "ponumber": ("ponumber", "pono", "purchaseordernumber", "purchaseorder"),
    "podate": ("podate", "purchaseorderdate"),
    "poamount": ("poamount", "purchaseorderamount"),
    "invoiceamount": ("invoiceamount", "invoicetotal", "amountdue", "totaldue", "total"),
    "invoicetaxamount": ("invoicetaxamount", "taxamount", "vat", "tax"),
    "currency": ("currency",),
    "supplier": ("supplier", "vendorname", "vendor"),
    "buyer": ("buyer", "billto", "billedto"),
    "supplieraddress": ("supplieraddress",),
    "shiptoaddress": ("shiptoaddress", "shipto"),
    "paytoaddress": ("paytoaddress", "payto"),
    "terms": ("paymentterms", "termandconditions", "terms"),
    "documenttype": ("documenttype",),
    "matchedstatus": ("matchedstatus",),
}

_DOC_TYPES = ("INVOICE", "CREDIT NOTE", "CREDIT MEMO", "PURCHASE ORDER", "RECEIPT")
_COMPANY = re.compile(r"\b(ltd|limited|inc|corp|llc|gmbh|plc|co\.?)\b", re.I)
_MONTH_DATE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _compact(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _in_text(value: str, text: str) -> bool:
    if not value or not text:
        return False
    folded_value = re.sub(r"\s+", " ", value).strip().lower()
    folded_text = re.sub(r"\s+", " ", text).strip().lower()
    return folded_value in folded_text


def _normalize_date(value: str) -> str | None:
    raw = (value or "").strip()
    iso = _ISO_DATE.search(raw)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    month = _MONTH_DATE.search(raw)
    if not month:
        return None
    number = _MONTHS[month.group(1).lower()]
    day = int(month.group(2))
    year = int(month.group(3))
    if not 1 <= day <= 31:
        return None
    return f"{year:04d}-{number:02d}-{day:02d}"


def _labeled_values(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_c = _compact(key)
        value = value.strip().strip("\"'")
        if key_c and value and key_c != _compact(value) and key_c not in found:
            found[key_c] = value
    return found


def _company_name(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for index, line in enumerate(lines):
        if not _COMPANY.search(line):
            continue
        previous = lines[index - 1] if index else ""
        if previous and len(previous.split()) <= 3 and ":" not in previous and not _COMPANY.search(previous):
            candidate = f"{previous} {line}".strip()
        else:
            candidate = line
        if _in_text(previous, text) and _in_text(line, text):
            return re.sub(r"\s+", " ", candidate)
    return ""


def _amount_after_total(text: str) -> str:
    match = re.search(
        r"(?:^|\n)\s*total\s*\n(?:[^\n]*\n){0,2}\s*(\$\s?\d[\d,]*(?:\.\d{2})?)",
        text or "",
        re.I,
    )
    if not match:
        return ""
    amount = re.sub(r"\s+", "", match.group(1))
    return amount if _in_text(amount.replace("$", ""), text) or _in_text(amount, text) else ""


def _document_type(text: str) -> str:
    for line in (text or "").splitlines():
        token = line.strip().upper()
        if token in _DOC_TYPES and _in_text(token, text):
            return token
    return ""


def _value_for(field_name: str, field_type: str, labels: dict[str, str], text: str) -> str | None:
    key = _compact(field_name)
    for alias in _LABEL_ALIASES.get(key, (key,)):
        raw = labels.get(alias)
        if not raw or not _in_text(raw, text):
            continue
        if field_type.upper() == "DATE":
            return _normalize_date(raw)
        return raw
    if key == "documenttype":
        return _document_type(text) or None
    if key == "supplier":
        return _company_name(text) or None
    if key == "invoiceamount":
        return _amount_after_total(text) or None
    return None


def fill_null_fields(fields: list[dict[str, Any]], ocr_text: str) -> list[dict[str, Any]]:
    """Replace null values when the OCR text contains that field's label."""
    if not fields or not (ocr_text or "").strip():
        return fields
    labels = _labeled_values(ocr_text)
    filled: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, dict):
            filled.append(field)
            continue
        current = field.get("value")
        if current not in (None, ""):
            filled.append(field)
            continue
        value = _value_for(
            str(field.get("name") or ""),
            str(field.get("type") or "SHORT_TEXT"),
            labels,
            ocr_text,
        )
        if not value:
            filled.append(field)
            continue
        filled.append({**field, "value": value})
    return filled
