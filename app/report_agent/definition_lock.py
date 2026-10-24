"""Lock LLM reportDefinition against live schema — never invent identifiers."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.report_agent.metadata_service import DatabaseSchema
from app.summary_skills.lock import loads_json_object

logger = logging.getLogger("orchestrator.report_agent.definition_lock")

_AGG = frozenset({"none", "count", "sum", "avg", "min", "max"})
_OPS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "is_null", "not_null", "not_deleted"}
)
_JOIN_TYPES = frozenset({"inner", "left"})
_CATALOG_TABLES = frozenset(
    {
        "wworkflow",
        "wworkflows",
        "workflows",
        "workflowdefinitions",
        "wrepository",
        "wrepositories",
        "repositories",
        "repositorydefinitions",
        "wform",
        "wforms",
        "forms",
        "formdefinitions",
        "ezfb_forms",
    }
)
_SOFT_DELETE = frozenset({"isdeleted", "is_deleted"})
_STATUS_LEAVES = frozenset(
    {"status", "matched_status", "stage", "state", "approvalstatus", "matchedstatus"}
)
_EXPR_FUNCS = frozenset(
    {
        "sum",
        "count",
        "avg",
        "min",
        "max",
        "coalesce",
        "cast",
        "null",
        "true",
        "false",
        "as",
        "case",
        "when",
        "then",
        "else",
        "end",
        "and",
        "or",
        "not",
        "between",
        "current_date",
        "current_timestamp",
        "now",
        "age",
        "extract",
        "greatest",
        "least",
        "date",
        "timestamp",
        "integer",
        "numeric",
        "text",
        "boolean",
        "interval",
        "day",
    }
)
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b")
_EXPR_HINT = re.compile(
    r"[+\-*/()]|\b(case|when|sum|count|avg|min|max|coalesce|cast)\b",
    re.IGNORECASE,
)


@dataclass
class _TableIndex:
    schema_name: str
    table: str
    columns: dict[str, str]  # lower -> original case


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


def is_catalog_table(table: Optional[str]) -> bool:
    return (table or "").strip().strip('"').lower() in _CATALOG_TABLES


def is_fact_table(table: Optional[str]) -> bool:
    t = (table or "").strip().strip('"').lower()
    if not t or is_catalog_table(t):
        return False
    return (
        (t.startswith("ezfb_") and "_items" in t)
        or t.startswith("items_")
        or "items_" in t
        or t.startswith("inbox_")
        or t.startswith("workflow_instances_")
        or t.startswith("workflow_tasks_")
    )


def _index_schema(schema: DatabaseSchema) -> dict[str, _TableIndex]:
    """Map table key (qualified lower / bare lower) -> original names + columns."""
    index: dict[str, _TableIndex] = {}
    for (_schema_key, _table_key), cols in schema.columns_by_table.items():
        if not cols:
            continue
        orig_s = cols[0].schema
        orig_t = cols[0].table
        entry = _TableIndex(
            schema_name=orig_s,
            table=orig_t,
            columns={c.column.lower(): c.column for c in cols},
        )
        index[orig_t.lower()] = entry
        index[f"{orig_s.lower()}.{orig_t.lower()}"] = entry
    return index


def _resolve_table(
    schema_name: Optional[str],
    table: str,
    index: dict[str, _TableIndex],
) -> Optional[_TableIndex]:
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
            return index[key]
    return None


def _split_ref(ref: str) -> tuple[str, str]:
    text = (ref or "").strip().strip('"')
    if "." in text:
        alias, col = text.rsplit(".", 1)
        return alias.strip(), col.strip()
    return "", text


def _looks_like_expr(text: str) -> bool:
    return bool(_EXPR_HINT.search(text or ""))


def _leaf_name(ref: str) -> str:
    return _split_ref(ref)[1].lower()


def lock_report_definition(
    raw: dict[str, Any],
    schema: DatabaseSchema,
    *,
    report_type: Optional[str] = None,
    preferred_schema: Optional[str] = None,
    preferred_table: Optional[str] = None,
) -> dict[str, Any]:
    """Validate and sanitize LLM definition against live schema."""
    warnings: list[str] = []
    if isinstance(raw.get("warnings"), list):
        warnings.extend(str(w) for w in raw["warnings"] if w)

    index = _index_schema(schema)
    preferred_entry = None
    if preferred_table:
        preferred_entry = _resolve_table(preferred_schema, preferred_table, index)
    if preferred_entry is None:
        for live_schema, live_table in schema.tables:
            if not is_fact_table(live_table):
                continue
            preferred_entry = _resolve_table(live_schema, live_table, index)
            if preferred_entry:
                break

    alias_to_table: dict[str, _TableIndex] = {}
    sources_out: list[dict[str, Any]] = []
    orig_alias: dict[str, str] = {}

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
        if not alias:
            alias = resolved.table
        alias_l = alias.lower()
        alias_to_table[alias_l] = resolved
        orig_alias[alias_l] = alias
        sources_out.append(
            {"alias": alias, "schemaName": resolved.schema_name or None, "table": resolved.table}
        )

    def _canonical_field(ref: str) -> Optional[str]:
        alias, col = _split_ref(ref)
        if not col:
            return None
        if alias:
            entry = alias_to_table.get(alias.lower())
            if not entry:
                entry = _resolve_table(None, alias, index)
                if not entry:
                    return None
            real = entry.columns.get(col.lower())
            if not real:
                return None
            used_alias = orig_alias.get(alias.lower()) or alias
            return f"{used_alias}.{real}"
        for src in sources_out:
            entry = alias_to_table.get(str(src["alias"]).lower())
            if not entry:
                continue
            real = entry.columns.get(col.lower())
            if real:
                return f"{src['alias']}.{real}"
        return None

    def _canonical_expr(expr: str) -> Optional[str]:
        text = (expr or "").strip()
        if not text:
            return None
        if not _looks_like_expr(text):
            return _canonical_field(text)
        invalid = False

        def repl(match: re.Match[str]) -> str:
            nonlocal invalid
            token = match.group(0)
            if token.lower() in _EXPR_FUNCS or token.replace(".", "", 1).isdigit():
                return token
            canon = _canonical_field(token if "." in token else token)
            if not canon:
                invalid = True
                return token
            return canon

        out = _IDENT_RE.sub(repl, text)
        return None if invalid else out

    def _field_ok(ref: str) -> bool:
        if _looks_like_expr(ref):
            return _canonical_expr(ref) is not None
        return _canonical_field(ref) is not None

    joins_out: list[dict[str, Any]] = []
    for join in raw.get("joins") or []:
        if not isinstance(join, dict):
            continue
        left = _canonical_field(str(join.get("left") or ""))
        right = _canonical_field(str(join.get("right") or ""))
        jtype = str(join.get("type") or "left").lower()
        if jtype not in _JOIN_TYPES:
            jtype = "left"
        if left and right:
            joins_out.append({"left": left, "right": right, "type": jtype})
        else:
            warnings.append(
                f"Dropped invalid join {join.get('left')} = {join.get('right')}."
            )

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
        canon = _canonical_expr(source) if _looks_like_expr(source) else _canonical_field(source)
        if not canon and agg != "count":
            warnings.append(f"Dropped unknown column source '{source}'.")
            continue
        columns_out.append(
            {"key": key, "label": label, "source": canon or source, "aggregate": agg}
        )

    filters_out: list[dict[str, Any]] = []
    for filt in raw.get("filters") or []:
        if not isinstance(filt, dict):
            continue
        field = str(filt.get("field") or "").strip()
        op = str(filt.get("op") or "eq").lower()
        value = filt.get("value")
        if op not in _OPS:
            warnings.append(f"Dropped filter with invalid op '{op}'.")
            continue
        canon = _canonical_field(field)
        if not canon:
            warnings.append(f"Dropped unknown filter field '{field}'.")
            continue
        leaf = _leaf_name(canon)
        if op == "eq" and leaf in _SOFT_DELETE and value in (False, 0, "0", "false", "False", "FALSE", "f"):
            op = "not_deleted"
            value = None
        elif op == "eq" and isinstance(value, str) and (
            leaf in _STATUS_LEAVES or "status" in leaf
        ):
            stripped = value.strip()
            if " " in stripped or len(stripped) > 24:
                warnings.append(
                    f"Status filter '{stripped}' looks like a description phrase; "
                    "using not-null instead of equality."
                )
                op = "not_null"
                value = None
        filters_out.append({"field": canon, "op": op, "value": value})

    group_by_out: list[str] = []
    col_by_key = {str(c.get("key") or "").lower(): c for c in columns_out}
    for g in raw.get("groupBy") or raw.get("group_by") or []:
        gtext = str(g).strip()
        if not gtext:
            continue
        canon = _canonical_field(gtext)
        if canon:
            group_by_out.append(canon)
            continue
        col = col_by_key.get(gtext.lower())
        if col and col.get("source"):
            group_by_out.append(str(col["source"]))
            continue
        if _looks_like_expr(gtext):
            expr = _canonical_expr(gtext)
            if expr:
                group_by_out.append(expr)
                continue
        warnings.append(f"Dropped unknown groupBy '{g}'.")

    order_by_out: list[dict[str, Any]] = []
    for ob in raw.get("orderBy") or raw.get("order_by") or []:
        if not isinstance(ob, dict):
            continue
        field = str(ob.get("field") or "").strip()
        direction = str(ob.get("direction") or "asc").lower()
        if direction not in ("asc", "desc"):
            direction = "asc"
        keys = {c["key"].lower() for c in columns_out}
        canon = _canonical_field(field)
        if canon:
            order_by_out.append({"field": canon, "direction": direction})
        elif field.lower() in keys:
            order_by_out.append({"field": field, "direction": direction})
        else:
            warnings.append(f"Dropped unknown orderBy '{field}'.")

    available_filters: list[dict[str, Any]] = []
    for af in raw.get("availableFilters") or raw.get("available_filters") or []:
        if not isinstance(af, dict):
            continue
        field = str(af.get("field") or "").strip()
        canon = _canonical_field(field) if field else None
        if field and not canon:
            warnings.append(f"Dropped availableFilter '{field}'.")
            continue
        available_filters.append(
            {
                "key": str(af.get("key") or field),
                "label": str(af.get("label") or af.get("key") or field),
                "field": canon,
                "type": str(af.get("type") or "text"),
            }
        )

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    title = str(raw.get("title") or "Report").strip() or "Report"
    rt = str(raw.get("reportType") or raw.get("report_type") or report_type or "").strip() or None

    _inject_soft_delete(sources_out, alias_to_table, orig_alias, filters_out)

    if not sources_out:
        warnings.append("No valid source tables remained after schema lock.")
    if not columns_out:
        warnings.append("No valid columns remained after schema lock.")

    locked = {
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

    if preferred_entry and _should_relocate(sources_out, preferred_entry):
        grounded = definition_from_table(
            schema,
            preferred_entry.schema_name,
            preferred_entry.table,
            title=title,
            report_type=rt,
            column_hints=columns_out or raw.get("columns") or [],
            filter_hints=filters_out,
            group_by_hints=group_by_out or raw.get("groupBy") or raw.get("group_by") or [],
        )
        if grounded:
            extra = list(warnings)
            extra.append(
                "Redirected report source from catalog/definition table to "
                f"{preferred_entry.schema_name}.{preferred_entry.table}."
            )
            grounded["warnings"] = extra + [w for w in (grounded.get("warnings") or []) if w not in extra]
            return grounded

    return locked


def _should_relocate(sources_out: list[dict[str, Any]], preferred: _TableIndex) -> bool:
    if not sources_out:
        return True
    if any(
        str(s.get("table") or "").lower() == preferred.table.lower()
        and str(s.get("schemaName") or "").lower() == preferred.schema_name.lower()
        for s in sources_out
    ):
        return False
    if any(is_fact_table(str(s.get("table") or "")) for s in sources_out):
        return False
    return all(is_catalog_table(str(s.get("table") or "")) for s in sources_out)


def _inject_soft_delete(
    sources_out: list[dict[str, Any]],
    alias_to_table: dict[str, _TableIndex],
    orig_alias: dict[str, str],
    filters_out: list[dict[str, Any]],
) -> None:
    for src in sources_out:
        alias = str(src.get("alias") or "")
        entry = alias_to_table.get(alias.lower())
        if not entry:
            continue
        real = next((entry.columns[k] for k in entry.columns if k in _SOFT_DELETE), None)
        if not real:
            continue
        field = f"{orig_alias.get(alias.lower()) or alias}.{real}"
        if any(_leaf_name(str(f.get("field") or "")) in _SOFT_DELETE for f in filters_out):
            continue
        filters_out.append({"field": field, "op": "not_deleted", "value": None})


def definition_from_table(
    schema: DatabaseSchema,
    schema_name: Optional[str],
    table: str,
    *,
    title: Optional[str] = None,
    report_type: Optional[str] = None,
    column_hints: Optional[list[Any]] = None,
    filter_hints: Optional[list[Any]] = None,
    group_by_hints: Optional[list[Any]] = None,
) -> Optional[dict[str, Any]]:
    """Build a locked definition against one live table, preserving identifier case."""
    index = _index_schema(schema)
    entry = _resolve_table(schema_name, table, index)
    if not entry:
        return None
    alias = "t"
    hints: list[str] = []
    formulas: dict[str, str] = {}
    for item in column_hints or []:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("source") or "").strip()
            source = str(item.get("source") or item.get("formula") or key).strip()
            if key:
                hints.append(key)
            if source and _looks_like_expr(source):
                formulas[key.lower() or source.lower()] = source
        elif item:
            hints.append(str(item))

    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hint in hints:
        leaf = _leaf_name(hint) or hint.lower()
        formula = formulas.get(leaf) or formulas.get(hint.lower())
        if formula:
            expr = formula
            invalid = False
            agg = "none"
            compact = formula.lower().replace(" ", "")
            inner = formula
            if compact.startswith("count("):
                agg = "count"
                inner = formula[formula.find("(") + 1 : formula.rfind(")")] or "1"
                if inner.strip() in ("*", ""):
                    inner = "1"
            elif compact.startswith("sum("):
                agg = "sum"
                inner = formula[formula.find("(") + 1 : formula.rfind(")")]
            expr = inner if agg in ("count", "sum") else formula

            def repl(match: re.Match[str], _entry: _TableIndex = entry) -> str:
                nonlocal invalid
                token = match.group(0)
                if token.lower() in _EXPR_FUNCS or token.replace(".", "", 1).isdigit():
                    return token
                col = token.rsplit(".", 1)[-1]
                real = _entry.columns.get(col.lower())
                if not real:
                    invalid = True
                    return token
                return f"{alias}.{real}"

            canon = expr if agg == "count" and expr == "1" else _IDENT_RE.sub(repl, expr)
            if not invalid:
                key = hint.rsplit(".", 1)[-1] if "." in hint else hint
                columns.append(
                    {
                        "key": key,
                        "label": key.replace("_", " "),
                        "source": canon,
                        "aggregate": agg,
                    }
                )
                seen.add(key.lower())
                continue
        real = entry.columns.get(leaf)
        if not real or real.lower() in seen:
            continue
        columns.append(
            {
                "key": real,
                "label": real.replace("_", " "),
                "source": f"{alias}.{real}",
                "aggregate": "none",
            }
        )
        seen.add(real.lower())
        if len(columns) >= 12:
            break

    if not columns:
        preferred = [
            "invoice_no",
            "invoiceno",
            "po_number",
            "ponumber",
            "supplier",
            "vendor",
            "matched_status",
            "status",
            "po_amount",
            "invoice_amount",
            "amount",
            "name",
            "filename",
            "stage",
        ]
        for pref in preferred:
            real = entry.columns.get(pref)
            if real and real.lower() not in seen:
                columns.append(
                    {
                        "key": real,
                        "label": real.replace("_", " "),
                        "source": f"{alias}.{real}",
                        "aggregate": "none",
                    }
                )
                seen.add(real.lower())
            if len(columns) >= 8:
                break
    if not columns:
        for real in list(entry.columns.values())[:8]:
            columns.append(
                {
                    "key": real,
                    "label": real.replace("_", " "),
                    "source": f"{alias}.{real}",
                    "aggregate": "none",
                }
            )

    filters: list[dict[str, Any]] = []
    for filt in filter_hints or []:
        if not isinstance(filt, dict):
            continue
        field = str(filt.get("field") or "").strip()
        leaf = _leaf_name(field) or field.lower()
        real = entry.columns.get(leaf)
        if not real:
            continue
        op = str(filt.get("op") or "eq").lower()
        if op not in _OPS:
            continue
        value = filt.get("value")
        if op == "eq" and leaf in _SOFT_DELETE:
            op = "not_deleted"
            value = None
        elif op == "eq" and isinstance(value, str) and (leaf in _STATUS_LEAVES or "status" in leaf):
            if " " in value.strip() or len(value.strip()) > 24:
                op = "not_null"
                value = None
        filters.append({"field": f"{alias}.{real}", "op": op, "value": value})

    group_by: list[str] = []
    col_by_key = {str(c.get("key") or "").lower(): c for c in columns}
    for g in group_by_hints or []:
        gtext = str(g).strip()
        if not gtext:
            continue
        leaf = _leaf_name(gtext) or gtext.lower()
        col = col_by_key.get(gtext.lower()) or col_by_key.get(leaf)
        if col and col.get("source"):
            group_by.append(str(col["source"]))
            continue
        real = entry.columns.get(leaf)
        if real:
            group_by.append(f"{alias}.{real}")

    real_del = next((entry.columns[k] for k in entry.columns if k in _SOFT_DELETE), None)
    if real_del and not any(_leaf_name(str(f.get("field") or "")) in _SOFT_DELETE for f in filters):
        filters.append({"field": f"{alias}.{real_del}", "op": "not_deleted", "value": None})

    available = [
        {
            "key": c["key"],
            "label": c["label"],
            "field": c["source"] if not _looks_like_expr(str(c.get("source") or "")) else None,
            "type": "text",
        }
        for c in columns[:6]
        if not _looks_like_expr(str(c.get("source") or ""))
    ]

    return {
        "title": (title or "Report").strip() or "Report",
        "reportType": report_type,
        "sources": [
            {"alias": alias, "schemaName": entry.schema_name or None, "table": entry.table}
        ],
        "joins": [],
        "columns": columns,
        "filters": filters,
        "groupBy": group_by,
        "orderBy": [{"field": columns[0]["source"], "direction": "asc"}] if columns else [],
        "availableFilters": available,
        "summary": {},
        "warnings": [],
    }


def merge_spec_into_definition(raw: dict[str, Any], spec: Any) -> dict[str, Any]:
    """Overlay Phase 1 reportSpec source table, columns, and formulas onto Phase 2 JSON."""
    out = dict(raw or {})
    source_table = getattr(spec, "source_table", None) if spec is not None else None
    source_schema = getattr(spec, "source_schema", None) if spec is not None else None
    spec_columns = list(getattr(spec, "columns", None) or []) if spec is not None else []
    spec_filters = list(getattr(spec, "filters", None) or []) if spec is not None else []

    sources = [s for s in (out.get("sources") or []) if isinstance(s, dict)]
    if source_table and (
        not sources or all(is_catalog_table(str(s.get("table") or "")) for s in sources)
    ):
        out["sources"] = [
            {"alias": "t", "schemaName": source_schema, "table": source_table}
        ]
        out["joins"] = []

    if spec_columns:
        cols: list[dict[str, Any]] = []
        for col in spec_columns:
            key = str(getattr(col, "key", "") or "").strip()
            if not key:
                continue
            formula = getattr(col, "formula", None)
            label = str(getattr(col, "label", None) or key)
            source = str(formula or key)
            agg = "none"
            compact = source.lower().replace(" ", "")
            if compact.startswith("count("):
                agg = "count"
                inner = source[source.find("(") + 1 : source.rfind(")")]
                source = "1" if inner.strip() in ("*", "") else inner
            elif compact.startswith("sum("):
                agg = "sum"
                source = source[source.find("(") + 1 : source.rfind(")")]
            cols.append({"key": key, "label": label, "source": source, "aggregate": agg})
        out["columns"] = cols

    out["filters"] = [
        {
            "field": getattr(f, "field", None),
            "op": getattr(f, "op", None) or "eq",
            "value": getattr(f, "value", None),
        }
        for f in spec_filters
        if getattr(f, "field", None)
    ]

    group_by = [str(g).strip() for g in (getattr(spec, "group_by", None) or []) if str(g).strip()]
    if group_by:
        out["groupBy"] = group_by

    title = str(getattr(spec, "title", "") or "").strip()
    if title:
        out["title"] = title

    sort = getattr(spec, "sort", None)
    if sort and getattr(sort, "field", None):
        out["orderBy"] = [
            {
                "field": str(sort.field).strip(),
                "direction": str(getattr(sort, "direction", None) or "asc"),
            }
        ]
    return out


def fallback_definition_from_prompt(
    prompt: str,
    schema: DatabaseSchema,
    *,
    report_type: Optional[str] = None,
    title: Optional[str] = None,
    preferred_schema: Optional[str] = None,
    preferred_table: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Best-effort schema-backed definition when the model returns broken JSON."""
    text = prompt or ""
    if not text.strip() or not schema.tables:
        return None

    mentioned: list[tuple[str, str]] = []
    if preferred_table:
        index = _index_schema(schema)
        entry = _resolve_table(preferred_schema, preferred_table, index)
        if entry:
            mentioned.append((entry.schema_name, entry.table))
    for schema_name, table in schema.tables:
        for candidate in (
            f"{schema_name}.{table}",
            table,
            f"dbo.{table}" if schema_name.lower() != "dbo" else table,
        ):
            if candidate.lower() in text.lower():
                mentioned.append((schema_name, table))
                break
    if not mentioned:
        for schema_name, table in schema.tables:
            if is_fact_table(table):
                mentioned.append((schema_name, table))
        mentioned = mentioned[:1]

    if not mentioned:
        return None

    schema_name, table = mentioned[0]
    column_hints: list[str] = []
    cols = schema.get_columns_for_table(schema_name, table)
    col_by_lower = {c.column.lower(): c.column for c in cols}
    for lower_name, real_name in col_by_lower.items():
        if re.search(rf"\b{re.escape(real_name)}\b", text, flags=re.IGNORECASE):
            column_hints.append(real_name)
        elif re.search(rf"\b{re.escape(lower_name)}\b", text, flags=re.IGNORECASE):
            column_hints.append(real_name)

    grounded = definition_from_table(
        schema,
        schema_name,
        table,
        title=title,
        report_type=report_type,
        column_hints=column_hints,
    )
    if grounded is None:
        return None
    grounded["warnings"] = [
        "Used schema-backed fallback definition because the model returned invalid JSON."
    ]
    return grounded
