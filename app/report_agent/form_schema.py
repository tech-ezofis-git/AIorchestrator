"""Extract report columns from an EZOFIS formJson (designer controls)."""
from __future__ import annotations

import json
import re
from typing import Any

_FIELD_TYPES = frozenset(
    {
        "SHORT_TEXT",
        "LONG_TEXT",
        "NUMBER",
        "INTEGER",
        "DECIMAL",
        "CURRENCY",
        "CURRENCY_AMOUNT",
        "BOOLEAN",
        "DATE",
        "DATETIME",
        "TIME",
        "EMAIL",
        "PHONE_NUMBER",
        "PHONE",
        "SINGLE_SELECT",
        "MULTI_SELECT",
        "DROPDOWN",
        "RADIO",
        "CHECKBOX",
        "CALCULATED",
        "PERCENTAGE",
        "USER",
        "LOOKUP",
        "URL",
        "SEQUENCE",
        "AUTO_NUMBER",
        "FORMULA",
    }
)
_CONTAINER_TYPES = frozenset({"TABLE", "DYNAMIC_TABLE", "REPEATABLE", "GRID"})
_SKIP_LABELS = frozenset(
    {
        "#",
        "field",
        "left_parantheses",
        "left_parentheses",
        "right_parantheses",
        "right_parentheses",
        "multiply",
        "add",
        "subtract",
        "divide",
    }
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.I,
)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _label_of(node: dict[str, Any]) -> str:
    return str(
        node.get("label")
        or node.get("displayLabel")
        or node.get("DisplayLabel")
        or node.get("name")
        or node.get("Name")
        or ""
    ).strip()


def _nested_column_name(node: dict[str, Any]) -> str:
    settings = node.get("settings") if isinstance(node.get("settings"), dict) else {}
    general = settings.get("general") if isinstance(settings.get("general"), dict) else {}
    for candidate in (
        node.get("columnName"),
        node.get("ColumnName"),
        settings.get("columnName"),
        general.get("columnName"),
        node.get("jsonId"),
        node.get("JsonId"),
    ):
        text = str(candidate or "").strip()
        if text and not _UUID_RE.match(text):
            return text
    return ""


def column_key_from_label(label: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", (label or "").strip()).strip("_")
    return key or "Column"


def extract_form_fields(form_json: Any) -> list[dict[str, str]]:
    """Return header-level form controls as {key, label, type}.

    Nested TABLE/DYNAMIC_TABLE children are skipped so line-item grids do not
    flood the report column list. Calculation AST nodes are ignored.
    """
    root = _parse_json(form_json)
    fields: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(node: dict[str, Any]) -> None:
        typ = str(node.get("type") or node.get("Type") or "").upper()
        if typ not in _FIELD_TYPES:
            return
        label = _label_of(node)
        if not label or label.lower() in _SKIP_LABELS:
            return
        key = _nested_column_name(node) or column_key_from_label(label)
        low = key.lower()
        if low in seen:
            return
        seen.add(low)
        if typ in {
            "NUMBER",
            "INTEGER",
            "DECIMAL",
            "CURRENCY",
            "CURRENCY_AMOUNT",
            "PERCENTAGE",
            "CALCULATED",
            "FORMULA",
        }:
            data_type = "numeric"
        elif typ in {"DATE", "DATETIME", "TIME"}:
            data_type = "date"
        else:
            data_type = "text"
        fields.append({"key": key, "label": label, "type": data_type})

    def _walk(node: Any) -> None:
        node = _parse_json(node)
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if not isinstance(node, dict):
            return
        typ = str(node.get("type") or node.get("Type") or "").upper()
        if typ in _CONTAINER_TYPES:
            return
        if typ in _FIELD_TYPES:
            _add(node)
            return
        for value in node.values():
            if isinstance(value, (dict, list, str)):
                _walk(value)

    _walk(root)
    return fields


def extract_repository_fields(fields: Any) -> list[dict[str, str]]:
    """Return repository designer fields as {key, label, type}."""
    rows = fields if isinstance(fields, list) else []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for field in rows:
        if not isinstance(field, dict):
            continue
        typ = str(field.get("type") or field.get("dataType") or "").upper()
        if typ in _CONTAINER_TYPES:
            continue
        label = str(field.get("name") or field.get("label") or "").strip()
        if not label or label.lower() in _SKIP_LABELS:
            continue
        key = str(field.get("sqlColumnName") or field.get("columnName") or "").strip()
        if not key or _UUID_RE.match(key):
            key = column_key_from_label(label)
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        if typ in {"NUMBER", "INTEGER", "DECIMAL", "CURRENCY", "CURRENCY_AMOUNT", "PERCENTAGE"}:
            data_type = "numeric"
        elif typ in {"DATE", "DATETIME", "TIME"}:
            data_type = "date"
        elif typ in {"BOOLEAN", "CHECKBOX"}:
            data_type = "boolean"
        else:
            data_type = "text"
        out.append({"key": key, "label": label, "type": data_type})
    if "filename" not in seen:
        out.insert(0, {"key": "FileName", "label": "File Name", "type": "text"})
    return out
