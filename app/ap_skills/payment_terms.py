"""Payment terms parsing and due-date derivation for AP invoices."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional

_NET_ONE_MONTH = re.compile(r"net\s*(?:one|1)\s*month", re.I)
_NET_DAYS = re.compile(r"net\s*(\d+)", re.I)
_DAYS_ONLY = re.compile(r"(\d+)\s*days?", re.I)
_TERMS_NOISE = re.compile(
    r"thank\s*you|payment\s*due\s*per|agreed\s*terms|for\s*your\s*business",
    re.I,
)


def looks_like_payment_terms(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or _TERMS_NOISE.search(text):
        return False
    lower = text.lower()
    if _NET_ONE_MONTH.search(lower) or _NET_DAYS.search(lower) or _DAYS_ONLY.search(lower):
        return True
    if lower in {"cod", "due on receipt", "due upon receipt", "prepaid"}:
        return True
    return False


def normalize_payment_terms(raw: Any) -> Optional[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text or not looks_like_payment_terms(text):
        return None
    lower = text.lower()
    if _NET_ONE_MONTH.search(lower):
        return {"basis": "NET", "net_days": 30, "discount": None}
    m = _NET_DAYS.search(lower)
    if m:
        return {"basis": "NET", "net_days": int(m.group(1)), "discount": None}
    m = _DAYS_ONLY.search(lower)
    if m:
        return {"basis": "NET", "net_days": int(m.group(1)), "discount": None}
    if "cod" in lower:
        return {"basis": "COD", "net_days": 0, "discount": None}
    if "due on receipt" in lower or "due upon receipt" in lower:
        return {"basis": "DUE_ON_RECEIPT", "net_days": 0, "discount": None}
    return {"basis": "OTHER", "net_days": None, "discount": None}


def parse_invoice_date(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(text, dayfirst=False, fuzzy=True)
    except Exception:
        return None


def due_date_from_terms(*, invoice_date: Any, terms: Any) -> Optional[str]:
    """Return YYYY-MM-DD due date from invoice date + payment terms, or None."""
    normalized = normalize_payment_terms(terms)
    if not normalized or normalized.get("net_days") is None:
        return None
    parsed = parse_invoice_date(invoice_date)
    if not parsed:
        return None
    return (parsed + timedelta(days=int(normalized["net_days"]))).date().isoformat()
