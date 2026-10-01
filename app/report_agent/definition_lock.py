"""Lock LLM reportDefinition against live schema — never invent identifiers."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.report_agent.metadata_service import DatabaseSchema

_AGG = frozenset({"none", "count", "sum", "avg", "min", "max"})
_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "is_null", "not_null"})
_JOIN_TYPES = frozenset({"inner", "left"})


def parse_definition_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model response for report definition.")
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
    if fence:
        raw = fence.group(1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Model response was not JSON.") from None
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Model JSON was not an object.")
    return payload


def _index_schema(schema: DatabaseSchema) -> dict[str, set[str]]:
    """Map table key (qualified lower / bare lower) -> set of column lowers."""
    index: dict[str, set[str]] = {}
    for (schema_name, table), cols in schema.columns_by_table.items():
        colset = {c.column.lower() for c in cols}
        index[table.lower()] = colset
        index[f"{schema_name.lower()}.{table.lower()}"] = colset
    return index


def _resolve_table(
    schema_name: Optional[str],
    table: str,
    index: dict[str, set[str]],
) -> Optional[tuple[str, str]]:
    table_clean = (table or "").strip().strip('"')
    schema_clean = (schema_name or "").strip().strip('"')
    if not table_clean:
        return None
    if "." in table_clean and not schema_clean:
        parts = table_clean.split(".", 1)
        schema_clean, table_clean = parts[0], parts[1]
    keys = []
    if schema_clean:
        keys.append(f"{schema_clean.lower()}.{table_clean.lower()}")
    keys.append(table_clean.lower())
    for key in keys:
        if key in index:
            if "." in key:
                s, t = key.split(".", 1)
                return s, t
            # find first matching schema
            for (s, t) in [(k.split(".", 1)[0], k.split(".", 1)[1]) for k in index if "." in k]:
                if t == table_clean.lower():
                    return s, t
            return "", table_clean
    return None


def _split_ref(ref: str) -> tuple[str, str]:
    text = (ref or "").strip().strip('"')
    if "." in text:
        alias, col = text.rsplit(".", 1)
        return alias.strip(), col.strip()
    return "", text


def lock_report_definition(
    raw: dict[str, Any],
    schema: DatabaseSchema,
    *,
    report_type: Optional[str] = None,
) -> dict[str, Any]:
    """Validate and sanitize LLM definition against live schema."""
    warnings: list[str] = []
    if isinstance(raw.get("warnings"), list):
        warnings.extend(str(w) for w in raw["warnings"] if w)

    index = _index_schema(schema)
    alias_to_table: dict[str, tuple[str, str]] = {}
    sources_out: list[dict[str, Any]] = []

    for src in raw.get("sources") or []:
        if not isinstance(src, dict):
            continue
        alias = str(src.get("alias") or src.get("table") or "").strip()
        table = str(src.get("table") or "").strip()
        schema_name = src.get("schemaName") if "schemaName" in src else src.get("schema_name")
        resolved = _resolve_table(str(schema_name) if schema_name else None, table, index)
        if not resolved:
            warnings.append(f"Dropped unknown source table '{table}'.")
            continue
        s, t = resolved
        if not alias:
            alias = t
        alias_l = alias.lower()
        alias_to_table[alias_l] = (s, t)
        sources_out.append({"alias": alias, "schemaName": s or None, "table": t})

    def _field_ok(ref: str) -> bool:
        alias, col = _split_ref(ref)
        if not col:
            return False
        if alias:
            table_key = alias_to_table.get(alias.lower())
            if not table_key:
                # treat alias as table name
                table_key = _resolve_table(None, alias, index)
                if not table_key:
                    return False
            s, t = table_key
            cols = index.get(f"{s}.{t}") or index.get(t) or set()
            return col.lower() in cols
        # bare column: must exist on some source
        for s, t in alias_to_table.values():
            cols = index.get(f"{s}.{t}") or index.get(t) or set()
            if col.lower() in cols:
                return True
        return False

    joins_out: list[dict[str, Any]] = []
    for join in raw.get("joins") or []:
        if not isinstance(join, dict):
            continue
        left = str(join.get("left") or "")
        right = str(join.get("right") or "")
        jtype = str(join.get("type") or "left").lower()
        if jtype not in _JOIN_TYPES:
            jtype = "left"
        if _field_ok(left) and _field_ok(right):
            joins_out.append({"left": left, "right": right, "type": jtype})
        else:
            warnings.append(f"Dropped invalid join {left} = {right}.")

    columns_out: list[dict[str, Any]] = []
    for col in raw.get("columns") or []:
        if not isinstance(col, dict):
            continue
        key = str(col.get("key") or "").strip()
        label = str(col.get("label") or key).strip()
        source = str(col.get("source") or "").strip()
        agg = str(col.get("aggregate") or "none").lower()
        if agg not in _AGG:
            agg = "none"
        if not key or not source:
            warnings.append(f"Dropped incomplete column {col}.")
            continue
        if agg == "count" and source.lower() in ("*", "1"):
            columns_out.append({"key": key, "label": label, "source": "1", "aggregate": "count"})
            continue
        if not _field_ok(source) and agg != "count":
            warnings.append(f"Dropped unknown column source '{source}'.")
            continue
        columns_out.append({"key": key, "label": label, "source": source, "aggregate": agg})

    filters_out: list[dict[str, Any]] = []
    for filt in raw.get("filters") or []:
        if not isinstance(filt, dict):
            continue
        field = str(filt.get("field") or "").strip()
        op = str(filt.get("op") or "eq").lower()
        if op not in _OPS:
            warnings.append(f"Dropped filter with invalid op '{op}'.")
            continue
        if op not in ("is_null", "not_null") and not _field_ok(field):
            warnings.append(f"Dropped unknown filter field '{field}'.")
            continue
        if op in ("is_null", "not_null") and not _field_ok(field):
            warnings.append(f"Dropped unknown filter field '{field}'.")
            continue
        filters_out.append({"field": field, "op": op, "value": filt.get("value")})

    group_by_out: list[str] = []
    for g in raw.get("groupBy") or raw.get("group_by") or []:
        g_s = str(g).strip()
        if _field_ok(g_s):
            group_by_out.append(g_s)
        else:
            warnings.append(f"Dropped unknown groupBy '{g_s}'.")

    order_by_out: list[dict[str, Any]] = []
    for ob in raw.get("orderBy") or raw.get("order_by") or []:
        if not isinstance(ob, dict):
            continue
        field = str(ob.get("field") or "").strip()
        direction = str(ob.get("direction") or "asc").lower()
        if direction not in ("asc", "desc"):
            direction = "asc"
        # allow column keys from columns_out
        keys = {c["key"].lower() for c in columns_out}
        if _field_ok(field) or field.lower() in keys:
            order_by_out.append({"field": field, "direction": direction})
        else:
            warnings.append(f"Dropped unknown orderBy '{field}'.")

    available_filters: list[dict[str, Any]] = []
    for af in raw.get("availableFilters") or raw.get("available_filters") or []:
        if not isinstance(af, dict):
            continue
        field = str(af.get("field") or "").strip()
        if field and not _field_ok(field):
            warnings.append(f"Dropped availableFilter '{field}'.")
            continue
        available_filters.append(
            {
                "key": str(af.get("key") or field),
                "label": str(af.get("label") or af.get("key") or field),
                "field": field or None,
                "type": str(af.get("type") or "text"),
            }
        )

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    title = str(raw.get("title") or "Report").strip() or "Report"
    rt = str(raw.get("reportType") or raw.get("report_type") or report_type or "").strip() or None

    if not sources_out:
        warnings.append("No valid source tables remained after schema lock.")
    if not columns_out:
        warnings.append("No valid columns remained after schema lock.")

    return {
        "title": title,
        "reportType": rt,
        "sources": sources_out,
        "joins": joins_out,
        "columns": columns_out,
        "filters": filters_out,
        "groupBy": group_by_out,
        "orderBy": order_by_out,
        "availableFilters": available_filters,
        "summary": summary,
        "warnings": warnings,
    }
