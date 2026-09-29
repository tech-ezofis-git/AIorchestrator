"""Turn InternalForm PO line JSON (keys are wFormControl jsonIds) into named rows."""
from __future__ import annotations

import json
from typing import Any, Optional

_SEMANTIC = (
    ("description", ("description", "itemdescription", "materialdescription")),
    ("qty", ("quantity", "qty", "orderquantity", "qtyshipped")),
    ("price", ("unitprice", "price", "rate", "unitcost", "netprice")),
    ("amount", ("amount", "lineamount", "extended", "extprice", "netvalue", "extendedamount")),
    ("line_no", ("lineno", "linenumber", "line", "itemnumber")),
    ("item_no", ("itemno", "partnumber", "materialid", "sku", "item")),
    ("uom", ("uom", "unitofmeasure", "unit")),
)


def _norm_label(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _parse_lines(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict) and row]


def _control_names(controls: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for control in controls or []:
        if not isinstance(control, dict):
            continue
        json_id = str(control.get("json_id") or control.get("jsonId") or "").strip()
        name = str(control.get("name") or control.get("column_name") or control.get("columnName") or "").strip()
        if json_id and name:
            names[json_id] = name
    return names


def _pick(named: dict[str, Any], *aliases: str) -> Any:
    by_norm = {_norm_label(key): value for key, value in named.items() if value not in (None, "")}
    for alias in aliases:
        if alias in by_norm:
            return by_norm[alias]
    return None


def decode_form_line_items(raw: Any, controls: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Map each PO line from control jsonIds onto description, qty, price, and amount.

    Ezofis stores the PO line grid as a JSON string whose keys are wFormControl
    jsonIds. Without the control names, line matching sees no description or
    amount and the invoice grid score stays 0.
    """
    rows = _parse_lines(raw)
    if not rows:
        return []
    id_to_name = _control_names(controls or [])
    decoded: list[dict[str, Any]] = []
    for row in rows:
        named: dict[str, Any] = {}
        for key, value in row.items():
            label = id_to_name.get(str(key)) or str(key)
            named[label] = value
        for semantic, aliases in _SEMANTIC:
            value = _pick(named, *aliases)
            if value not in (None, ""):
                named.setdefault(semantic, value)
        if any(named.get(key) not in (None, "") for key in ("description", "qty", "price", "amount")):
            decoded.append(named)
    return decoded
