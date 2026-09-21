"""Normalize GUID tenant / repository ids."""
from __future__ import annotations

import re
from uuid import UUID

_GUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def normalize_guid(value: object | None) -> str:
    """Return uppercase GUID string, or empty if missing."""
    if value is None:
        return ""
    if isinstance(value, UUID):
        return str(value).upper()
    text = str(value).strip().strip("{}")
    if not text:
        return ""
    return text.upper()


def is_guid(value: object | None) -> bool:
    text = normalize_guid(value)
    return bool(text and _GUID_RE.match(text))


def guid_prefix(value: object | None) -> str:
    """First hex segment of a GUID (e.g. 0B3E1B77)."""
    text = normalize_guid(value)
    if not text:
        return ""
    return text.split("-", 1)[0]
