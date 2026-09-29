"""Turn InternalForm PO line JSON (keys are wFormControl jsonIds) into named rows."""
from __future__ import annotations

import json
import re
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
    if decoded and any(row.get("description") for row in decoded):
        return decoded
    inferred = _infer_unlabeled_lines(rows)
    return inferred or decoded


def _column_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    return [str(row.get(key) or "").strip() for row in rows]


def _as_floats(values: list[str]) -> Optional[list[float]]:
    out: list[float] = []
    for value in values:
        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            return None
        out.append(float(value))
    return out


def _infer_unlabeled_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover description, qty, price, and amount when control names were not loaded.

    The same jsonId is a column on every row, so the values themselves show which
    column is the description, the quantity, the unit price, and the extended amount.
    """
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(str(key))
    roles: dict[str, str] = {}
    numeric: list[str] = []
    for key in keys:
        values = _column_values(rows, key)
        if not values or any(not value for value in values):
            continue
        if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or re.fullmatch(r"\d{1,3}%", value) for value in values):
            continue
        if all(re.search(r"[A-Za-z]", value) and " " in value for value in values):
            roles[key] = "description"
            continue
        if all(re.fullmatch(r"[A-Za-z]{2,4}", value) for value in values):
            roles[key] = "uom"
            continue
        if all(re.fullmatch(r"[A-Z][A-Z0-9]{3,}", value) for value in values) and any(
            any(ch.isdigit() for ch in value) for value in values
        ):
            roles[key] = "item_no"
            continue
        if _as_floats(values) is not None:
            numeric.append(key)

    line_key = None
    for key in numeric:
        values = _as_floats(_column_values(rows, key)) or []
        if values == [float(index) for index in range(1, len(rows) + 1)]:
            line_key = key
            roles[key] = "line_no"
            break
    rest = [key for key in numeric if key != line_key]
    qty_key = price_key = amount_key = None
    for qty_candidate in rest:
        qty_values = _as_floats(_column_values(rows, qty_candidate)) or []
        for price_candidate in rest:
            if price_candidate == qty_candidate:
                continue
            price_values = _as_floats(_column_values(rows, price_candidate)) or []
            for amount_candidate in rest:
                if amount_candidate in (qty_candidate, price_candidate):
                    continue
                amount_values = _as_floats(_column_values(rows, amount_candidate)) or []
                if all(
                    abs((qty_values[index] * price_values[index]) - amount_values[index]) < 0.05
                    for index in range(len(rows))
                ):
                    qty_key, price_key, amount_key = qty_candidate, price_candidate, amount_candidate
                    break
            if qty_key:
                break
        if qty_key:
            break
    if qty_key:
        roles[qty_key] = "qty"
    if price_key:
        roles[price_key] = "price"
    if amount_key:
        roles[amount_key] = "amount"
    if "description" not in roles.values():
        return []

    decoded: list[dict[str, Any]] = []
    for row in rows:
        named = dict(row)
        for key, role in roles.items():
            named[role] = row.get(key)
        decoded.append(named)
    return decoded
