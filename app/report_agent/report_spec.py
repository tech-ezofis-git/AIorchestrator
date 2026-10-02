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
from app.report_agent.schema_scope import SchemaTableSlice


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
    avail = available_columns_from_tables(tables)
    lower_map = {c.lower(): c for c in avail}
    desc = (description or "").strip()
    picked: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", desc):
        hit = lower_map.get(token.lower())
        if hit and hit not in picked:
            picked.append(hit)
        if len(picked) >= 8:
            break
    if not picked:
        for preferred in (
            "Invoice_No",
            "PO_Number",
            "Supplier",
            "Matched_Status",
            "PO_Amount",
            "Invoice_Amount",
            "status",
            "name",
            "fileName",
        ):
            hit = lower_map.get(preferred.lower())
            if hit and hit not in picked:
                picked.append(hit)
            if len(picked) >= 6:
                break
    if not picked:
        picked = avail[:6]

    columns = [ReportSpecColumn(key=k, label=_label_from_key(k)) for k in picked]
    # common total formula when both amount fields exist
    amount_keys = {c.lower(): c for c in avail}
    po = amount_keys.get("po_amount")
    inv = amount_keys.get("invoice_amount")
    if po and inv and not any(c.key.lower() == "total_amount" for c in columns):
        columns.append(
            ReportSpecColumn(
                key="Total_Amount",
                label="Total Amount",
                formula=f"{po} + {inv}",
            )
        )

    filters: list[ReportSpecFilter] = []
    matched = amount_keys.get("matched_status") or lower_map.get("status")
    if matched and re.search(r"approv|match", desc, re.I):
        filters.append(ReportSpecFilter(field=matched, op="not_null", value=None))

    warn = list(warnings or [])
    if "score" in desc.lower() and "score" not in lower_map:
        warn.append("Score column not found in schema — not included.")

    return ReportSpec(
        title=desc[:80] or "Report",
        objective=desc or "Generate the requested report.",
        report_type=report_type,
        columns=columns,
        filters=filters,
        group_by=[],
        sort=ReportSpecSort(field=columns[-1].key, direction="desc") if columns else None,
        available_columns=avail,
        warnings=warn,
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
        name = getattr(col, "name", None) or ""
        if name and name.lower() not in lower:
            lower[name.lower()] = name
            avail.append(name)

    kept: list[ReportSpecColumn] = []
    warnings = list(spec.warnings or [])
    for col in spec.columns:
        if col.formula:
            kept.append(col)
            continue
        hit = lower.get(col.key.lower())
        if hit:
            kept.append(ReportSpecColumn(key=hit, label=col.label or _label_from_key(hit), formula=None, editable=col.editable))
        else:
            warnings.append(f"Column '{col.key}' not found in schema slice; dropped from short prompt.")
    if not kept and avail:
        kept = [ReportSpecColumn(key=a, label=_label_from_key(a)) for a in avail[:6]]

    filters: list[ReportSpecFilter] = []
    for f in spec.filters:
        hit = lower.get(f.field.lower())
        if hit:
            filters.append(ReportSpecFilter(field=hit, op=f.op, value=f.value, editable=f.editable))
        else:
            warnings.append(f"Filter field '{f.field}' not found in schema slice; dropped.")

    group_by = []
    for g in spec.group_by:
        hit = lower.get(g.lower())
        if hit:
            group_by.append(hit)

    sort = spec.sort
    if sort and sort.field:
        # sort may target a formula column
        formula_keys = {c.key.lower() for c in kept if c.formula}
        hit = lower.get(sort.field.lower())
        if hit:
            sort = ReportSpecSort(field=hit, direction=sort.direction)
        elif sort.field.lower() not in formula_keys:
            warnings.append(f"Sort field '{sort.field}' not found; cleared.")
            sort = None

    return ReportSpec(
        title=spec.title,
        objective=spec.objective,
        report_type=spec.report_type,
        columns=kept,
        filters=filters,
        group_by=group_by,
        sort=sort,
        available_columns=avail[:80],
        warnings=warnings,
    )
