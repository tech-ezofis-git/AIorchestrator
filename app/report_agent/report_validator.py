"""Report validation service — validates query results against the Report Plan."""
from __future__ import annotations

import logging
from typing import Any
from app.models.report_agent import ReportData, ReportPlan, ReportValidation

logger = logging.getLogger("orchestrator.report_agent.report_validator")


def validate_report_data(
    report_plan: ReportPlan,
    report_data: ReportData,
    sql_is_valid: bool = True,
    sql_errors: list[str] | None = None,
) -> ReportValidation:
    """Validate query results against the ReportPlan specifications."""
    errors: list[str] = list(sql_errors or [])
    warnings: list[str] = []
    checks: dict[str, bool] = {
        "sql_safety": sql_is_valid,
        "columns_present": True,
        "calculations_present": True,
        "types_compatible": True,
        "filters_satisfied": True,
        "semantic_match": True,
        "row_count_valid": True,
    }

    if not sql_is_valid:
        checks["sql_safety"] = False

    returned_cols = set(report_data.columns)

    # 1. Check all planned columns
    missing_cols = []
    for col in report_plan.columns:
        if col.field not in returned_cols:
            missing_cols.append(col.field)

    if missing_cols:
        checks["columns_present"] = False
        warnings.append(f"Planned columns not found in returned result: {', '.join(missing_cols)}.")

    # 2. Check calculations
    missing_calcs = []
    for calc in report_plan.calculations:
        if calc.label not in returned_cols:
            missing_calcs.append(calc.label)

    if missing_calcs:
        checks["calculations_present"] = False
        warnings.append(f"Calculated fields not found in returned result: {', '.join(missing_calcs)}.")

    # 3. Check semantic domain alignment
    t_id = (report_plan.template_id or "").lower()
    planned_fields_lower = [c.field.lower() for c in report_plan.columns] + [c.label.lower() for c in report_plan.calculations]
    
    if "accounts-payable" in t_id:
        has_amount = any(any(k in f for k in ("amount", "balance", "total", "cost", "price", "credit")) for f in planned_fields_lower)
        has_date = any(any(k in f for k in ("due", "date", "aging", "day")) for f in planned_fields_lower)
        if not (has_amount and has_date):
            checks["semantic_match"] = False
            warnings.append("Report plan lacks essential Accounts Payable fields (requires both monetary amount and date/aging fields).")
    elif "workflow" in t_id:
        has_workflow_id = any(any(k in f for k in ("workflow", "request", "stage", "status", "instance", "step", "task", "process")) for f in planned_fields_lower)
        if not has_workflow_id:
            checks["semantic_match"] = False
            warnings.append("Report plan lacks essential Workflow identifiers or status fields.")
    elif "credit" in t_id or "roi" in t_id:
        has_credit = any(any(k in f for k in ("credit", "charge", "cost", "saving", "roi", "value", "execution")) for f in planned_fields_lower)
        if not has_credit:
            checks["semantic_match"] = False
            warnings.append("Report plan lacks essential credit consumption or financial metric fields.")

    # 4. Check types and empty rows
    if report_data.row_count == 0:
        warnings.append("The report query executed successfully but returned 0 rows for the current filter criteria.")
    else:
        # Check type compatibility on first few rows
        for row in report_data.rows[:10]:
            for col in report_plan.columns:
                val = row.get(col.field)
                if val is not None:
                    if col.type == "number" and not isinstance(val, (int, float)):
                        try:
                            float(str(val).replace("$", "").replace(",", "").strip())
                        except (ValueError, TypeError):
                            checks["types_compatible"] = False
                            warnings.append(f"Column '{col.field}' expected number, but encountered non-numeric value: '{val}'.")
                            break

    # 5. Check filter satisfaction
    if report_plan.filters and report_data.rows:
        for filt in report_plan.filters:
            if filt.operator == "IS NOT NULL":
                null_count = sum(1 for r in report_data.rows if r.get(filt.field) is None)
                if null_count > 0:
                    checks["filters_satisfied"] = False
                    warnings.append(f"Filter IS NOT NULL for '{filt.field}' had {null_count} null rows in result.")
            elif filt.operator == "IN" and isinstance(filt.value, (list, tuple, set)):
                allowed = set(str(v).lower() for v in filt.value)
                unexpected = sum(1 for r in report_data.rows if r.get(filt.field) is not None and str(r.get(filt.field)).lower() not in allowed)
                if unexpected > 0:
                    checks["filters_satisfied"] = False
                    warnings.append(f"Filter IN for '{filt.field}' had {unexpected} rows outside target set.")

    is_overall_valid = len(errors) == 0 and checks.get("semantic_match", True)

    return ReportValidation(
        valid=is_overall_valid,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )
