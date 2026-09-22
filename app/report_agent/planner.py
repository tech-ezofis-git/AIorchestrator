"""Report planner — builds a structured ReportPlan from template intent, discovered schema, and sampled data."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.models.report_agent import (
    DiscoveredField,
    PromptIntent,
    ReportCalculation,
    ReportColumn,
    ReportFilter,
    ReportPlan,
    ReportSort,
    ReportSource,
)
from app.report_agent.field_discovery import find_relevant_fields
from app.report_agent.metadata_service import DatabaseSchema
from app.report_agent.templates import TemplateDefinition, get_template

logger = logging.getLogger("orchestrator.report_agent.planner")


def _format_label(col_name: str) -> str:
    """Turn database column name into a clean, human-readable label."""
    # Handle camelCase, snake_case, PascalCase, or prefix like 'iFileName' -> 'File Name'
    s = col_name
    if s.startswith("i") and len(s) > 2 and s[1].isupper():
        s = s[1:]
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = s.replace("_", " ").replace("-", " ").strip()
    return " ".join(word.capitalize() for word in s.split())


def _infer_column_type(db_type: str) -> str:
    """Map PostgreSQL / SQL database data types strictly to report column types."""
    t = (db_type or "").lower()
    if "int" in t or "float" in t or "double" in t or "numeric" in t or "decimal" in t or "real" in t or "money" in t:
        return "number"
    if "timestamp" in t or "timestamptz" in t or "datetime" in t:
        return "timestamp"
    if "date" in t or "time" in t:
        return "date"
    if "bool" in t:
        return "boolean"
    if "uuid" in t:
        return "uuid"
    return "text"


def create_report_plan(
    template: TemplateDefinition,
    schema: DatabaseSchema,
    discovered_tables: list[str],
    discovered_fields: list[DiscoveredField],
    sampled_values: Optional[dict[str, Any]] = None,
    prompt_intent: Optional[PromptIntent] = None,
) -> ReportPlan:
    """Construct a structured, realistic ReportPlan based on template intent and real DB fields."""
    sampled_values = sampled_values or {}
    distinct_vals = sampled_values.get("distinct_values", {})

    # 1. Determine primary table
    primary_table_ident = discovered_tables[0] if discovered_tables else ""
    if not primary_table_ident and schema.tables:
        t_meta = schema.tables[0]
        primary_table_ident = f"{t_meta[0]}.{t_meta[1]}" if t_meta[0] != "public" else t_meta[1]

    parts = primary_table_ident.split(".")
    schema_name = parts[0] if len(parts) > 1 else None
    table_name = parts[-1] if parts else "report_source"

    source = ReportSource(
        table=table_name,
        schema_name=schema_name,
        joins=[],
    )

    # 2. Extract columns for the primary table
    table_fields = [
        f for f in discovered_fields
        if f.table.lower() == primary_table_ident.lower()
        or f.table.split(".")[-1].lower() == table_name.lower()
    ]
    if not table_fields and discovered_fields:
        table_fields = discovered_fields

    # Map to ReportColumn strictly preserving database data type
    columns: list[ReportColumn] = []
    seen_col_names: set[str] = set()
    for f in table_fields[:12]:  # Top 12 columns for neat tabular view
        if f.column.lower() in seen_col_names:
            continue
        seen_col_names.add(f.column.lower())
        c_type = _infer_column_type(f.type)
        c_fmt = None
        f_lower = f.column.lower()
        if any(k in f_lower for k in ("amount", "price", "cost", "total", "balance", "credit")):
            c_fmt = "currency"
        elif any(k in f_lower for k in ("date", "due", "time")) or c_type in ("date", "timestamp"):
            c_fmt = "YYYY-MM-DD HH:mm:ss" if c_type == "timestamp" else "YYYY-MM-DD"

        columns.append(
            ReportColumn(
                field=f.column,
                label=_format_label(f.column),
                type=c_type,
                format=c_fmt,
            )
        )

    filters: list[ReportFilter] = []
    calculations: list[ReportCalculation] = []
    order_by: list[ReportSort] = []
    group_by: list[str] = []
    status_rules: list[dict[str, Any]] = []

    # Identify candidate date, status, and numeric columns from selected table
    date_cols = [c.field for c in columns if c.type in ("date", "timestamp") or any(k in c.field.lower() for k in ("date", "due", "time"))]
    status_cols = [
        c.field for c in columns
        if any(k in c.field.lower() for k in ("status", "state", "stage", "is_completed", "is_deleted"))
    ]
    numeric_cols = [c.field for c in columns if c.type == "number" or any(k in c.field.lower() for k in ("amount", "total", "price", "cost", "balance", "credit", "count"))]

    vendor_cols = [
        c.field for c in columns
        if any(k in c.field.lower() for k in ("vendor", "supplier", "payee", "merchant", "company", "provider"))
    ]
    user_cols = [
        c.field for c in columns
        if any(k in c.field.lower() for k in ("user", "created_by", "modified_by", "owner", "author", "assignee"))
    ]

    # 3. Template-specific business rules & calculations
    t_id = template.id

    if t_id == "tpl-accounts-payable-aging":
        # Strictly prioritize payment terms due date over creation timestamps
        due_col = next((c for c in date_cols if "due" in c.lower()), None)
        if not due_col:
            # Fallback to invoice date or general date (excluding created_at / modified_at if possible)
            due_col = next((c for c in date_cols if "invoice" in c.lower() and "date" in c.lower()), None)
        if not due_col:
            due_col = next((c for c in date_cols if not any(k in c.lower() for k in ("created", "modified", "updated", "at"))), None)

        status_col = status_cols[0] if status_cols else None

        if due_col:
            calculations.append(
                ReportCalculation(
                    label="Aging Bucket",
                    expression=(
                        f"CASE "
                        f"WHEN CURRENT_DATE - CAST(\"{due_col}\" AS DATE) <= 30 THEN '0-30 Days' "
                        f"WHEN CURRENT_DATE - CAST(\"{due_col}\" AS DATE) <= 60 THEN '31-60 Days' "
                        f"WHEN CURRENT_DATE - CAST(\"{due_col}\" AS DATE) <= 90 THEN '61-90 Days' "
                        f"ELSE '90+ Days' END"
                    ),
                    type="text",
                )
            )
            calculations.append(
                ReportCalculation(
                    label="Days Past Due",
                    expression=f"GREATEST(0, CURRENT_DATE - CAST(\"{due_col}\" AS DATE))",
                    type="number",
                )
            )
            order_by.append(ReportSort(field=due_col, direction="ASC"))

        if status_col:
            # Check actual distinct status values
            actual_statuses = distinct_vals.get(status_col, [])
            pending_vals = [s for s in actual_statuses if any(p in s.lower() for p in ("pending", "open", "unpaid", "awaiting", "draft", "in review", "unmatched", "partially matched"))]
            if pending_vals:
                filters.append(ReportFilter(field=status_col, operator="IN", value=pending_vals))
            elif actual_statuses:
                filters.append(ReportFilter(field=status_col, operator="IN", value=actual_statuses[:5]))
            else:
                filters.append(ReportFilter(field=status_col, operator="IS NOT NULL", value=None))

    elif t_id == "tpl-pending-workflow-requests":
        status_col = next((c for c in status_cols if "status" in c.lower() or "state" in c.lower()), status_cols[0] if status_cols else None)
        if status_col:
            actual_statuses = distinct_vals.get(status_col, [])
            pending_vals = [s for s in actual_statuses if any(p in s.lower() for p in ("pending", "open", "in progress", "assigned", "active", "submitted", "review"))]
            if pending_vals:
                filters.append(ReportFilter(field=status_col, operator="IN", value=pending_vals))
            elif actual_statuses:
                # If all available, filter out completed/rejected
                open_vals = [s for s in actual_statuses if not any(c in s.lower() for c in ("complete", "closed", "reject", "cancel"))]
                filters.append(ReportFilter(field=status_col, operator="IN", value=open_vals or actual_statuses[:3]))
            else:
                filters.append(ReportFilter(field=status_col, operator="IS NOT NULL", value=None))

        if date_cols:
            order_by.append(ReportSort(field=date_cols[0], direction="DESC"))

    elif t_id == "tpl-workflow-sla-compliance":
        # Check start and completion dates
        start_col = next((c for c in date_cols if any(k in c.lower() for k in ("start", "create", "submit"))), date_cols[0] if date_cols else None)
        end_col = next((c for c in date_cols if any(k in c.lower() for k in ("complete", "finish", "end", "modify", "update"))), None)

        if start_col and end_col:
            calculations.append(
                ReportCalculation(
                    label="Turnaround Hours",
                    expression=f"ROUND(CAST(EXTRACT(EPOCH FROM (CAST(\"{end_col}\" AS TIMESTAMP) - CAST(\"{start_col}\" AS TIMESTAMP))) / 3600.0 AS NUMERIC), 2)",
                    type="number",
                )
            )
            calculations.append(
                ReportCalculation(
                    label="SLA Compliance Status",
                    expression=(
                        f"CASE "
                        f"WHEN EXTRACT(EPOCH FROM (CAST(\"{end_col}\" AS TIMESTAMP) - CAST(\"{start_col}\" AS TIMESTAMP))) <= 86400 THEN 'Within SLA' "
                        f"ELSE 'SLA Breached' END"
                    ),
                    type="text",
                )
            )
            filters.append(ReportFilter(field=end_col, operator="IS NOT NULL", value=None))
            order_by.append(ReportSort(field=end_col, direction="DESC"))
        elif date_cols:
            order_by.append(ReportSort(field=date_cols[0], direction="DESC"))

    elif t_id == "tpl-documents-without-recent-access":
        date_col = next((c for c in date_cols if any(k in c.lower() for k in ("access", "view", "modify", "update", "create"))), date_cols[0] if date_cols else None)
        if date_col:
            calculations.append(
                ReportCalculation(
                    label="Days Inactive",
                    expression=f"CURRENT_DATE - CAST(\"{date_col}\" AS DATE)",
                    type="number",
                )
            )
            order_by.append(ReportSort(field=date_col, direction="ASC"))

    elif t_id == "tpl-document-retention-status":
        status_col = status_cols[0] if status_cols else None
        date_col = date_cols[0] if date_cols else None
        if status_col:
            status_rules.append({"field": status_col, "rule": "Categorize documents into Active, Archived, or Retention Hold."})
        if date_col:
            order_by.append(ReportSort(field=date_col, direction="DESC"))

    elif t_id == "tpl-document-version-activity":
        ver_col = next((c.field for c in columns if any(k in c.field.lower() for k in ("version", "ver", "rev"))), None)
        date_col = date_cols[0] if date_cols else None
        if ver_col:
            order_by.append(ReportSort(field=ver_col, direction="DESC"))
        elif date_col:
            order_by.append(ReportSort(field=date_col, direction="DESC"))

    elif t_id == "tpl-portal-submission-performance":
        date_col = date_cols[0] if date_cols else None
        if date_col:
            order_by.append(ReportSort(field=date_col, direction="DESC"))

    elif t_id == "tpl-user-login-and-security-activity":
        date_col = date_cols[0] if date_cols else None
        if date_col:
            order_by.append(ReportSort(field=date_col, direction="DESC"))

    elif t_id == "tpl-ai-credit-consumption":
        credits_col = next((c for c in numeric_cols if any(k in c.lower() for k in ("credit", "cost", "usage", "charge", "amount"))), None)
        date_col = date_cols[0] if date_cols else None
        if credits_col:
            order_by.append(ReportSort(field=credits_col, direction="DESC"))
        elif date_col:
            order_by.append(ReportSort(field=date_col, direction="DESC"))

    elif t_id == "tpl-report-agent-roi":
        numeric_col = numeric_cols[0] if numeric_cols else None
        if numeric_col:
            calculations.append(
                ReportCalculation(
                    label="Estimated Savings ($)",
                    expression=f"ROUND(CAST(\"{numeric_col}\" * 4.5 AS NUMERIC), 2)",
                    type="number",
                )
            )
            order_by.append(ReportSort(field=numeric_col, direction="DESC"))
        elif date_cols:
            order_by.append(ReportSort(field=date_cols[0], direction="DESC"))

    # 4. Resolve PromptIntent (if provided by user edit)
    plan_title = template.title
    plan_description = template.description
    plan_warnings: list[str] = []

    if prompt_intent:
        if prompt_intent.title and prompt_intent.title.strip():
            plan_title = prompt_intent.title.strip()
        if prompt_intent.description and prompt_intent.description.strip():
            plan_description = prompt_intent.description.strip()
        if prompt_intent.warnings:
            plan_warnings.extend(prompt_intent.warnings)

        # 4a. Apply Prompt-Requested Filters
        if prompt_intent.requested_filters:
            for bf in prompt_intent.requested_filters:
                if bf.concept == "status":
                    if status_cols:
                        target_col = status_cols[0]
                        actual_statuses = distinct_vals.get(target_col, [])
                        req_vals = bf.value if isinstance(bf.value, list) else [bf.value]
                        matched_vals = [s for s in actual_statuses if any(rv.lower() in str(s).lower() for rv in req_vals)]
                        filters = [f for f in filters if f.field != target_col]
                        if matched_vals:
                            filters.append(ReportFilter(field=target_col, operator="IN", value=matched_vals))
                        elif req_vals:
                            filters.append(ReportFilter(field=target_col, operator="IN", value=req_vals))
                        else:
                            filters.append(ReportFilter(field=target_col, operator="IS NOT NULL", value=None))
                    else:
                        plan_warnings.append(f"Could not apply status filter '{bf.raw_text}': no status field exists in the live schema.")

                elif bf.concept in ("due_date", "date_range"):
                    target_col = None
                    if bf.concept == "due_date" or "due" in (bf.raw_text or "").lower():
                        target_col = next((c for c in date_cols if "due" in c.lower()), None)
                    if not target_col:
                        target_col = date_cols[0] if date_cols else None
                    if target_col:
                        filters = [f for f in filters if f.field != target_col]
                        filters.append(ReportFilter(field=target_col, operator=bf.operator, value=bf.value))
                    else:
                        plan_warnings.append(f"Could not apply date filter '{bf.raw_text}': no date field exists in the live schema.")

                else:
                    # General / Numeric filter (e.g. amount > 1000, total_amount >= 500, sla_hours < 24)
                    c_lower = bf.concept.lower().strip()
                    matched_col = next(
                        (c.field for c in columns if c.field.lower() == c_lower or c_lower in c.field.lower()),
                        None,
                    )
                    if not matched_col and any(k in c_lower for k in ("amount", "total", "price", "cost", "balance", "credit")):
                        matched_col = numeric_cols[0] if numeric_cols else None

                    if matched_col:
                        # Remove prior filter on the same column if any
                        filters = [f for f in filters if f.field != matched_col]
                        filters.append(ReportFilter(field=matched_col, operator=bf.operator, value=bf.value))
                    else:
                        plan_warnings.append(f"Could not apply filter '{bf.raw_text}': no '{bf.concept}' field exists in the live schema.")

        # 4b. Apply Prompt-Requested Group By
        if prompt_intent.group_by_concepts:
            for raw_c in prompt_intent.group_by_concepts:
                c = raw_c.lower().strip()
                matched_field = None
                if any(k in c for k in ("vendor", "supplier", "payee", "merchant", "provider")):
                    matched_field = vendor_cols[0] if vendor_cols else None
                elif any(k in c for k in ("user", "author", "owner", "assignee", "creator")):
                    matched_field = user_cols[0] if user_cols else None
                elif any(k in c for k in ("status", "state", "stage")):
                    matched_field = status_cols[0] if status_cols else None
                elif any(k in c for k in ("date", "day", "month", "year", "time")):
                    matched_field = date_cols[0] if date_cols else None
                else:
                    matched_field = next((col.field for col in columns if col.field.lower() == c or c in col.field.lower()), None)

                if matched_field:
                    if matched_field not in group_by:
                        group_by.append(matched_field)
                else:
                    plan_warnings.append(f"Could not apply 'group by {raw_c}': no {raw_c} field exists in the live schema.")

        # 4c. Apply Prompt-Requested Sorting
        if prompt_intent.sort_concepts:
            custom_sorts: list[ReportSort] = []
            for bs in prompt_intent.sort_concepts:
                c = bs.concept.lower().strip()
                matched_field = None
                if any(k in c for k in ("amount", "total", "cost", "price", "balance", "credit", "money", "sum", "value", "highest", "lowest")):
                    matched_field = numeric_cols[0] if numeric_cols else None
                elif any(k in c for k in ("date", "time", "created", "recent", "due")):
                    if "due" in c:
                        matched_field = next((d for d in date_cols if "due" in d.lower()), date_cols[0] if date_cols else None)
                    else:
                        matched_field = date_cols[0] if date_cols else None
                else:
                    matched_field = next((col.field for col in columns if col.field.lower() == c or c in col.field.lower()), None)

                if matched_field:
                    custom_sorts.append(ReportSort(field=matched_field, direction=bs.direction))
                else:
                    plan_warnings.append(f"Could not sort by '{bs.concept}': no matching field exists in the live schema.")

            if custom_sorts:
                order_by = custom_sorts

        # 4d. Apply Prompt-Requested Calculations
        if prompt_intent.requested_calculations:
            if "count" in prompt_intent.requested_calculations:
                if not any("count" in c.label.lower() for c in calculations):
                    calculations.append(ReportCalculation(label="Count", expression="COUNT(*)", type="number"))
            if "sum" in prompt_intent.requested_calculations and numeric_cols:
                if not any("total" in c.label.lower() or "sum" in c.label.lower() for c in calculations):
                    calculations.append(ReportCalculation(label=f"Total {_format_label(numeric_cols[0])}", expression=f"SUM(CAST(\"{numeric_cols[0]}\" AS NUMERIC))", type="number"))

    # Fallback default sort if none defined
    if not order_by and date_cols:
        order_by.append(ReportSort(field=date_cols[0], direction="DESC"))
    elif not order_by and columns:
        order_by.append(ReportSort(field=columns[0].field, direction="ASC"))

    return ReportPlan(
        title=plan_title,
        description=plan_description,
        template_id=template.id,
        source=source,
        columns=columns,
        filters=filters,
        calculations=calculations,
        group_by=group_by,
        order_by=order_by,
        status_rules=status_rules,
        warnings=plan_warnings,
    )
