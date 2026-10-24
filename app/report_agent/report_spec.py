"""Client-facing short reportSpec helpers (Phase 1 UI + Phase 2 input)."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.models.report_agent import (
    ReportSpec,
    ReportSpecColumn,
    ReportSpecFilter,
    ReportSpecSort,
)
from app.report_agent.definition_lock import parse_definition_json
from app.report_agent.metadata_service import DatabaseSchema
from app.report_agent.schema_scope import SchemaTableSlice, preferred_fact_table


def available_columns_from_tables(tables: list[SchemaTableSlice], *, limit: int = 80) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for table in tables:
        for col in table.columns or []:
            key = (col.name or "").strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            names.append(key)
            if len(names) >= limit:
                return names
    return names


def _label_from_key(key: str) -> str:
    text = re.sub(r"[_\-]+", " ", (key or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else "Column"


def normalize_report_spec(
    raw: Any,
    *,
    report_type: Optional[str] = None,
    description: str = "",
    available_columns: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
) -> ReportSpec:
    data: dict[str, Any] = {}
    if isinstance(raw, ReportSpec):
        data = raw.model_dump(by_alias=True)
    elif isinstance(raw, dict):
        data = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            data = parse_definition_json(raw)
        except ValueError:
            data = {"objective": raw.strip()}

    title = str(data.get("title") or description or "Report").strip() or "Report"
    objective = str(data.get("objective") or description or title).strip()

    columns: list[ReportSpecColumn] = []
    for item in data.get("columns") or []:
        if isinstance(item, str):
            key = item.strip()
            if not key:
                continue
            columns.append(ReportSpecColumn(key=key, label=_label_from_key(key)))
            continue
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("field") or "").strip()
        if not key:
            continue
        columns.append(
            ReportSpecColumn(
                key=key,
                label=str(item.get("label") or _label_from_key(key)).strip() or key,
                formula=(str(item["formula"]).strip() if item.get("formula") else None),
                editable=bool(item.get("editable", True)),
            )
        )

    filters: list[ReportSpecFilter] = []
    for item in data.get("filters") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("key") or "").strip()
        if not field:
            continue
        filters.append(
            ReportSpecFilter(
                field=field,
                op=str(item.get("op") or "eq").strip() or "eq",
                value=item.get("value"),
                editable=bool(item.get("editable", True)),
            )
        )

    group_by = [
        str(g).strip()
        for g in (data.get("groupBy") or data.get("group_by") or [])
        if str(g).strip()
    ]

    sort = None
    sort_raw = data.get("sort")
    if isinstance(sort_raw, dict) and sort_raw.get("field"):
        sort = ReportSpecSort(
            field=str(sort_raw.get("field")).strip(),
            direction=str(sort_raw.get("direction") or "asc").strip().lower() or "asc",
        )

    avail = available_columns
    if avail is None:
        avail = [str(c).strip() for c in (data.get("availableColumns") or data.get("available_columns") or []) if str(c).strip()]

    warn = list(warnings or [])
    for w in data.get("warnings") or []:
        text = str(w).strip()
        if text and text not in warn:
            warn.append(text)

    if not columns and description:
        # very light fallback: use words that look like field names from description
        pass

    return ReportSpec(
        title=title,
        objective=objective,
        report_type=str(data.get("reportType") or data.get("report_type") or report_type or "").strip() or None,
        columns=columns,
        filters=filters,
        group_by=group_by,
        sort=sort,
        available_columns=avail or [],
        source_schema=str(data.get("sourceSchema") or data.get("source_schema") or "").strip() or None,
        source_table=str(data.get("sourceTable") or data.get("source_table") or "").strip() or None,
        warnings=warn,
    )


def parse_report_spec_json(raw: str) -> ReportSpec:
    payload = parse_definition_json(raw)
    return normalize_report_spec(payload)


def fallback_report_spec(
    *,
    description: str,
    report_type: str,
    tables: list[SchemaTableSlice],
    warnings: Optional[list[str]] = None,
) -> ReportSpec:
    from app.report_agent.report_catalog import match_report_intent, spec_from_intent

    avail = available_columns_from_tables(tables)
    preferred = preferred_fact_table(tables)
    return spec_from_intent(
        intent=match_report_intent(description, report_type=report_type),
        description=description,
        report_type=report_type,
        available_columns=avail,
        source_schema=(preferred.schema_name if preferred else None),
        source_table=(preferred.table if preferred else None),
        warnings=warnings,
    )


def report_spec_to_prompt(
    spec: ReportSpec,
    *,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
) -> str:
    """Build a short executable prompt from client-edited reportSpec."""
    lines: list[str] = []
    lines.append(f"Report objective: {spec.objective or spec.title}")
    if spec.report_type:
        scope = f"Report type is {spec.report_type}."
        if workflow_name:
            scope += f" Workflow name is {workflow_name}."
        if repository_name:
            scope += f" Repository name is {repository_name}."
        lines.append(f"Report type and scope: {scope}")
    if spec.source_table:
        qualified = f"{spec.source_schema}.{spec.source_table}" if spec.source_schema else spec.source_table
        lines.append(
            f"Data sources: Use the table {qualified}. "
            "Do not query Workflows, Repositories, or wForm catalog tables for report rows."
        )
    if spec.columns:
        col_bits = []
        for c in spec.columns:
            bit = c.label or c.key
            if c.formula:
                bit += f" (formula: {c.formula})"
            col_bits.append(f"{c.key} as {bit}" if c.label and c.label != c.key else c.key)
        lines.append("Output columns: " + ", ".join(col_bits) + ".")
        lines.append("Required fields: " + ", ".join(c.key for c in spec.columns) + ".")
    if spec.filters:
        parts = []
        for f in spec.filters:
            if f.op == "not_null":
                parts.append(f"{f.field} is not null")
            elif f.value is None:
                parts.append(f"{f.field} {f.op}")
            else:
                parts.append(f"{f.field} {f.op} {f.value}")
        lines.append("Filters and conditions: " + "; ".join(parts) + ".")
    if spec.group_by:
        lines.append("Aggregations / grouping / sorting: Group by " + ", ".join(spec.group_by) + ".")
    if spec.sort and spec.sort.field:
        direction = "descending" if (spec.sort.direction or "").lower().startswith("desc") else "ascending"
        lines.append(f"Sort the results by {spec.sort.field} in {direction} order.")
    formulas = [c for c in spec.columns if c.formula]
    if formulas:
        lines.append(
            "Formulas: "
            + "; ".join(f"{c.key} = {c.formula}" for c in formulas)
            + "."
        )
    if spec.warnings:
        lines.append("Assumptions and gaps: " + " ".join(spec.warnings))
    lines.append("Pagination and filtering requirements: Support server-side pagination.")
    return "\n\n".join(lines)


def enrich_spec_with_schema(
    spec: ReportSpec,
    schema: DatabaseSchema,
    tables: list[SchemaTableSlice],
) -> ReportSpec:
    """Drop unknown columns when possible; keep formulas; fill availableColumns."""
    avail = available_columns_from_tables(tables)
    lower = {c.lower(): c for c in avail}
    # also allow any schema column name as available
    for col in getattr(schema, "all_columns", []) or []:
        name = getattr(col, "column", None) or getattr(col, "name", None) or ""
        if name and name.lower() not in lower:
            lower[name.lower()] = name
            avail.append(name)

    def _match_name(key: str) -> Optional[str]:
        if not key:
            return None
        hit = lower.get(key.lower())
        if hit:
            return hit
        needle = re.sub(r"[^a-z0-9]", "", key.lower())
        if len(needle) < 3:
            return None
        for low, real in lower.items():
            compact = re.sub(r"[^a-z0-9]", "", low)
            if needle == compact or needle in compact or compact in needle:
                return real
        return None

    virtual = (spec.source_table or "").lower() in {"workflow_instances_all", "items_all"}
    kept: list[ReportSpecColumn] = []
    warnings = list(spec.warnings or [])
    for col in spec.columns:
        if col.formula:
            kept.append(col)
            continue
        hit = _match_name(col.key)
        if hit:
            kept.append(ReportSpecColumn(key=hit, label=col.label or _label_from_key(hit), formula=None, editable=col.editable))
        elif virtual:
            kept.append(col)
        else:
            warnings.append(f"Column '{col.key}' not found in schema slice; dropped from short prompt.")
    if not kept and avail:
        kept = [ReportSpecColumn(key=a, label=_label_from_key(a)) for a in avail[:6]]

    filters: list[ReportSpecFilter] = []
    for f in spec.filters:
        hit = _match_name(f.field)
        if hit:
            filters.append(ReportSpecFilter(field=hit, op=f.op, value=f.value, editable=f.editable))
        elif virtual:
            filters.append(f)
        else:
            warnings.append(f"Filter field '{f.field}' not found in schema slice; dropped.")

    group_by = []
    formula_keys = {c.key.lower() for c in kept if c.formula}
    for g in spec.group_by:
        hit = _match_name(g)
        if hit:
            group_by.append(hit)
        elif g.lower() in formula_keys or virtual:
            group_by.append(g)

    sort = spec.sort
    if sort and sort.field:
        formula_keys = {c.key.lower() for c in kept if c.formula}
        hit = _match_name(sort.field)
        if hit:
            sort = ReportSpecSort(field=hit, direction=sort.direction)
        elif sort.field.lower() not in formula_keys:
            warnings.append(f"Sort field '{sort.field}' not found; cleared.")
            sort = None

    preferred = preferred_fact_table(tables)
    source_table = spec.source_table
    source_schema = spec.source_schema
    if not source_table and preferred:
        source_schema = preferred.schema_name
        source_table = preferred.table
    elif source_table:
        resolved = schema.resolve_table(source_schema, source_table) if hasattr(schema, "resolve_table") else None
        if resolved:
            source_schema, source_table = resolved

    return ReportSpec(
        title=spec.title,
        objective=spec.objective,
        report_type=spec.report_type,
        columns=kept,
        filters=filters,
        group_by=group_by,
        sort=sort,
        available_columns=avail[:80],
        source_schema=source_schema,
        source_table=source_table,
        warnings=warnings,
    )
