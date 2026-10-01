"""Expiry status for OCR fields: "Active · <time left>" / "Expired · <time since>" against today's date.

Only expiry-type fields (recognised by name) get a `status` key; every other field is returned
unchanged. A document is still Active on its expiry day.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

ACTIVE = "Active"
EXPIRED = "Expired"

_EXPIRY_NAMES = {
    "expiry", "expirydate", "dateofexpiry", "expirationdate", "dateofexpiration", "expdate",
    "validuntil", "validtill", "validupto", "validthru", "validthrough",
}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_expiry_field(name: str) -> bool:
    key = re.sub(r"[^a-z]", "", (name or "").lower())
    return key in _EXPIRY_NAMES or key.endswith(("expirydate", "dateofexpiry", "expirationdate"))


def apply_expiry_status(
    fields: list[dict[str, Any]], *, today: date, rename_to: Optional[str] = None
) -> list[dict[str, Any]]:
    """Add `status` to expiry fields; the first one with a readable date is renamed to `rename_to`."""
    out = []
    renamed = False
    for field in fields:
        if isinstance(field, dict) and is_expiry_field(str(field.get("name") or "")):
            field = {**field, **expiry_status(field.get("value"), today=today)}
            if rename_to and not renamed and field.get("status"):
                field["name"] = rename_to
                renamed = True
        out.append(field)
    return out


def expiry_status(value: Any, *, today: date) -> dict[str, Any]:
    """{"status": "Active · 10 years" / "Expired · 8 years"}; None when the value is unreadable."""
    expiry = _parse_date(value)
    if expiry is None:
        return {"status": None}
    days = (expiry - today).days
    if days < 0:
        return {"status": f"{EXPIRED} · {_span(-days)}"}
    return {"status": f"{ACTIVE} · {_span(days) if days else 'today'}"}


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not _ISO_DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _span(days: int) -> str:
    if days < 30:
        count, unit = days, "day"
    elif days < 365:
        count, unit = days // 30, "month"
    else:
        count, unit = days // 365, "year"
    return f"{count} {unit}{'' if count == 1 else 's'}"
