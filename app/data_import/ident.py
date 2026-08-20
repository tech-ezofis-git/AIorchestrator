"""SQL identifier quoting and ezfb_*_items table name from formId."""
from __future__ import annotations

import re
from typing import Any, Optional

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PO_TABLE_BODY_RE = re.compile(r"^ezfb_([a-zA-Z0-9-]+)_items$", re.I)
_PO_TABLE_ID_MAX_LEN = 64


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


pg_ident = quote_ident


def is_guid(value: Any) -> bool:
    if value is None:
        return False
    return bool(_GUID_RE.match(str(value).strip()))


def _normalize_po_table_body(raw: str) -> Optional[str]:
    if not raw:
        return None
    text = re.sub(r"^\[?dbo\]?\.", "", str(raw).strip(), flags=re.I).strip("[]").strip()
    match = _PO_TABLE_BODY_RE.match(text)
    if not match:
        return None
    token = match.group(1)
    if not token or len(token) > _PO_TABLE_ID_MAX_LEN:
        return None
    return text


def po_master_table_from_form_token(form_token: Any) -> Optional[str]:
    """v5: ezfb_98_items | v6: ezfb_<first 8 hex of form GUID>_items."""
    if form_token is None or form_token == "":
        return None
    token = str(form_token).strip()
    if is_guid(token):
        token = token.replace("-", "").lower()[:8]
    elif not re.match(r"^[a-zA-Z0-9-]+$", token):
        return None
    return _normalize_po_table_body(f"ezfb_{token}_items")


def strip_dbo_table_name(qualified: str) -> str:
    text = str(qualified).strip()
    return re.sub(r"^\[?dbo\]?\.", "", text, flags=re.I).strip("[]").strip()


def resolve_import_table_name(form_id: str) -> str:
    qualified = po_master_table_from_form_token(form_id)
    if not qualified:
        raise ValueError(f"Invalid formId for PO master table: {form_id!r}")
    return strip_dbo_table_name(qualified)


def parse_connection_kv(conn_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (conn_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        out[key.strip().lower()] = val.strip()
    return out
