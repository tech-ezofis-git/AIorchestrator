"""Lock LLM reportDefinition against live schema — never invent identifiers."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.report_agent.metadata_service import DatabaseSchema
from app.summary_skills.lock import loads_json_object

logger = logging.getLogger("orchestrator.report_agent.definition_lock")

_AGG = frozenset({"none", "count", "sum", "avg", "min", "max"})
_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "is_null", "not_null"})
_JOIN_TYPES = frozenset({"inner", "left"})


def parse_definition_json(text: str) -> dict[str, Any]:
    """Parse Phase 2 model output into a dict, tolerating common LLM JSON defects."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model response for report definition.")

    payload = loads_json_object(raw)
    if isinstance(payload, dict):
        return payload

    # Secondary salvage: strip fences / prose and retry with summary lock helpers
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    payload = loads_json_object(cleaned)
    if isinstance(payload, dict):
        return payload

    # Last attempt: find first object-looking block
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        payload = loads_json_object(cleaned[start : end + 1])
        if isinstance(payload, dict):
            return payload

    snippet = raw[:240].replace("\n", "\\n")
    logger.warning("report_definition_json_parse_failed", extra={"snippet": snippet})
    raise ValueError("Model response was not valid reportDefinition JSON.")


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
            for full in index:
                if "." in full and full.endswith("." + table_clean.lower()):
                    s, t = full.split(".", 1)
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
                table_key = _resolve_table(None, alias, index)
                if not table_key:
                    return False
            s, t = table_key
            cols = index.get(f"{s}.{t}") or index.get(t) or set()
            return col.lower() in cols
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
        if not _field_ok(field):
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


def fallback_definition_from_prompt(
    prompt: str,
    schema: DatabaseSchema,
    *,
    report_type: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Best-effort schema-backed definition when the model returns broken JSON."""
    text = prompt or ""
    if not text.strip() or not schema.tables:
        return None

    index = _index_schema(schema)
    # Prefer tables explicitly named in the prompt
    mentioned: list[tuple[str, str]] = []
    for schema_name, table in schema.tables:
        for candidate in (
            f"{schema_name}.{table}",
            table,
            f'dbo.{table}' if schema_name.lower() != "dbo" else table,
        ):
            if candidate.lower() in text.lower():
                mentioned.append((schema_name, table))
                break
    if not mentioned:
        # Prefer ezfb_* / items_* / inbox_* style business tables
        for schema_name, table in schema.tables:
            tl = table.lower()
            if tl.startswith(("ezfb_", "items_", "inbox_", "workflow_instances_")):
                mentioned.append((schema_name, table))
        mentioned = mentioned[:1]

    if not mentioned:
        return None

    schema_name, table = mentioned[0]
    cols = schema.get_columns_for_table(schema_name, table)
    if not cols:
        return None
    col_by_lower = {c.column.lower(): c.column for c in cols}

    # Collect field names that appear in the prompt and exist on the table
    token_hits: list[str] = []
    for lower_name, real_name in col_by_lower.items():
        # word-ish match for CamelCase / snake_case identifiers
        if re.search(rf"\b{re.escape(real_name)}\b", text, flags=re.IGNORECASE):
            token_hits.append(real_name)
        elif re.search(rf"\b{re.escape(lower_name)}\b", text, flags=re.IGNORECASE):
            token_hits.append(real_name)

    # Prefer useful business columns if none explicitly matched
    preferred = [
        "invoice_no",
        "invoiceno",
        "po_number",
        "ponumber",
        "supplier",
        "vendor",
        "currency",
        "po_date",
        "due_date",
        "matched_status",
        "status",
        "po_amount",
        "invoice_amount",
        "amount",
        "name",
        "stage",
    ]
    if not token_hits:
        for pref in preferred:
            if pref in col_by_lower:
                token_hits.append(col_by_lower[pref])

    # de-dupe preserve order
    seen: set[str] = set()
    fields: list[str] = []
    for name in token_hits:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        fields.append(name)
    fields = fields[:12]
    if not fields:
        fields = [c.column for c in cols[:8]]

    alias = "t"
    columns = [
        {
            "key": f,
            "label": f.replace("_", " "),
            "source": f"{alias}.{f}",
            "aggregate": "none",
        }
        for f in fields
    ]

    filters: list[dict[str, Any]] = []
    # Soft-delete if present
    for del_name in ("isDeleted", "is_deleted", "IsDeleted"):
        if del_name.lower() in col_by_lower:
            real = col_by_lower[del_name.lower()]
            filters.append({"field": f"{alias}.{real}", "op": "eq", "value": False})
            break
    # Matched_Status / status not-null when prompt mentions approved/matched
    if re.search(r"approved|matched_status|awaiting", text, flags=re.IGNORECASE):
        for status_name in ("Matched_Status", "matched_status", "status", "Status"):
            if status_name.lower() in col_by_lower:
                real = col_by_lower[status_name.lower()]
                filters.append({"field": f"{alias}.{real}", "op": "not_null", "value": None})
                break

    available = [
        {
            "key": f,
            "label": f.replace("_", " "),
            "field": f"{alias}.{f}",
            "type": "text",
        }
        for f in fields[:6]
    ]

    return {
        "title": (title or "Report").strip() or "Report",
        "reportType": report_type,
        "sources": [{"alias": alias, "schemaName": schema_name or None, "table": table}],
        "joins": [],
        "columns": columns,
        "filters": filters,
        "groupBy": [],
        "orderBy": [{"field": f"{alias}.{fields[0]}", "direction": "asc"}] if fields else [],
        "availableFilters": available,
        "summary": {},
        "warnings": [
            "Used schema-backed fallback definition because the model returned invalid JSON."
        ],
    }
