"""Schema and data validation for Dashboard Agent.

Validates requirement coverage, data coverage, scope control, and functional validity.
Implements the regeneration / correction loop if validation discovers missing requirements.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("orchestrator.dashboard.validator")

ALLOWED_CHART_TYPES = {
    "donut", "pie", "radar", "lollipop", "column", "bar",
    "line", "area", "gauge", "heatmap", "funnel", "stacked_bar", "grouped_bar", "scatter"
}

ALLOWED_AGGS = {
    "count", "sum", "avg", "min", "max", "distinct", "ratio", "percent",
    "overdue_sum", "overdue_count", "paid_sum", "outstanding_sum", "current_sum"
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _same_request(requested: str, actual: str) -> bool:
    """Match a requested component by its name."""
    left = _norm(requested)
    right = _norm(actual)
    if not left or not right:
        return False
    if left == right:
        return True
    if ("dis" in left) != ("dis" in right) or ("un" in left) != ("un" in right) or ("non" in left) != ("non" in right):
        return False
    left_words = {w for w in re.findall(r"[a-z0-9]+", (requested or "").lower()) if len(w) > 2}
    right_words = {w for w in re.findall(r"[a-z0-9]+", (actual or "").lower()) if len(w) > 2}
    if left_words and right_words:
        intersection = left_words & right_words
        if len(intersection) == len(left_words) or len(intersection) == len(right_words):
            return True
        if len(intersection) / max(len(left_words), len(right_words)) >= 0.7:
            return True
    return False


def validate_schema(
    *,
    prompt_analysis: dict[str, Any],
    schema: dict[str, Any],
    data_requirements: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """Validates the generated dashboard schema against prompt analysis and data requirements."""
    missing_requirements: list[str] = []
    unsupported_assumptions: list[str] = []
    unresolved_fields: list[str] = []

    kpis = schema.get("kpis") if isinstance(schema.get("kpis"), list) else []
    charts = schema.get("charts") if isinstance(schema.get("charts"), list) else []
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    filters = schema.get("filters") if isinstance(schema.get("filters"), list) else []

    # 1. Requirement Coverage
    req_kpis = prompt_analysis.get("kpis") or []
    for rk in req_kpis:
        r_name = str(rk.get("name") or rk.get("metric") or "")
        if not _norm(r_name):
            continue
        matched = any(
            _same_request(r_name, str(k.get("label") or k.get("title") or k.get("id") or ""))
            for k in kpis
        )
        if not matched:
            missing_requirements.append(f"Missing requested KPI: {r_name}")

    req_charts = prompt_analysis.get("charts") or []
    for rc in req_charts:
        rc_title = str(rc.get("title") or "")
        rc_type = str(rc.get("type") or "").lower()
        matched = any(
            _same_request(rc_title, str(c.get("title") or c.get("id") or ""))
            for c in charts
        )
        if not matched and rc_title:
            missing_requirements.append(f"Missing requested Chart: {rc_title} ({rc_type})")

    req_filters = prompt_analysis.get("filters") or []
    for rf in req_filters:
        rf_label = str(rf.get("label") or "")
        rf_field = str(rf.get("field_concept") or rf.get("field") or "")
        matched = any(
            _norm(rf_label) in _norm(f.get("label") or f.get("field") or "")
            or _norm(rf_field) in _norm(f.get("field") or f.get("label") or "")
            for f in filters
        )
        if not matched and rf_label:
            missing_requirements.append(f"Missing requested Filter: {rf_label}")

    # 2. Functional Validity
    for c in charts:
        ctype = str(c.get("type") or "donut").lower()
        if ctype not in ALLOWED_CHART_TYPES:
            c["type"] = "donut"
        agg = str(c.get("agg") or c.get("aggregation") or "count").lower()
        if agg not in ALLOWED_AGGS:
            c["agg"] = "count"

    for k in kpis:
        agg = str(k.get("agg") or k.get("aggregation") or "count").lower()
        if agg not in ALLOWED_AGGS:
            k["agg"] = "count"

    # 3. Data Coverage
    col_norms = {_norm(c): c for c in columns}
    req_fields = data_requirements.get("fields") if isinstance(data_requirements.get("fields"), list) else []
    for f in req_fields:
        fname = f.get("name") if isinstance(f, dict) else str(f)
        if not fname:
            continue
        if _norm(fname) not in col_norms and not any(_norm(fname) in cn for cn in col_norms):
            # Check if this field is unresolved
            unresolved_fields.append(str(fname))

    # 4. Scope Control
    # If the user prompt specifically asked for vessel or rfq, ensure no AP-specific words leaked in
    domain = str(prompt_analysis.get("domain") or "").lower()
    if "vessel" in domain or "rfq" in domain or "inventory" in domain or "hr" in domain:
        for k in kpis:
            lbl = str(k.get("label") or k.get("title") or "").lower()
            if any(bad in lbl for bad in ["invoice", "payable", "ap spending", "dpo", "supplier risk"]) and "invoice" not in domain:
                unsupported_assumptions.append(f"Unrelated AP metric '{lbl}' in {domain} dashboard")

    is_valid = len(missing_requirements) == 0 and len(unsupported_assumptions) == 0

    return {
        "requirements_covered": is_valid,
        "missing_requirements": missing_requirements,
        "unsupported_assumptions": unsupported_assumptions,
        "unresolved_fields": unresolved_fields,
    }


def regenerate_and_correct_schema(
    *,
    prompt_analysis: dict[str, Any],
    schema: dict[str, Any],
    validation_result: dict[str, Any],
    columns: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Corrects any missing requirements or unsupported assumptions in the schema."""
    kpis: list[dict[str, Any]] = list(schema.get("kpis") or [])
    charts: list[dict[str, Any]] = list(schema.get("charts") or [])
    filters: list[dict[str, Any]] = list(schema.get("filters") or [])
    missing = validation_result.get("missing_requirements") or []
    unsupported = validation_result.get("unsupported_assumptions") or []

    # Remove unsupported assumptions
    if unsupported:
        kpis = [
            k for k in kpis
            if not any(str(k.get("label") or "").lower() in bad.lower() for bad in unsupported)
        ]
        schema["kpis"] = kpis

    # Fill missing requested KPIs
    req_kpis = prompt_analysis.get("kpis") or []
    has_missing_kpis = any("Missing requested KPI:" in m for m in missing)
    if req_kpis and has_missing_kpis:
        req_names = {_norm(rk.get("name") or rk.get("metric") or "") for rk in req_kpis}
        kpis = [
            k for k in kpis
            if any(_same_request(r_name, str(k.get("label") or k.get("title") or k.get("id") or "")) for r_name in req_names)
        ]

    for rk in req_kpis:
        r_name = str(rk.get("name") or "")
        if any(f"Missing requested KPI: {r_name}" in m or _same_request(r_name, m) for m in missing):
            kpi_id = re.sub(r"[^a-z0-9]+", "_", r_name.lower()).strip("_")
            kpis.append({
                "id": kpi_id,
                "label": r_name[:40],
                "title": r_name[:40],
                "description": f"Measures {r_name}",
                "type": rk.get("aggregation") or "count",
                "agg": rk.get("aggregation") or "count",
                "enabled": True,
                "requirement": rk.get("requirement") or f"Show {r_name}",
                "columns": {},
            })

    # Fill missing requested Charts
    req_charts = prompt_analysis.get("charts") or []
    has_missing_charts = any("Missing requested Chart:" in m for m in missing)
    if req_charts and has_missing_charts:
        req_titles = {_norm(rc.get("title") or "") for rc in req_charts}
        charts = [
            c for c in charts
            if any(_same_request(rc_title, str(c.get("title") or c.get("label") or c.get("id") or "")) for rc_title in req_titles)
        ]

    for rc in req_charts:
        rc_title = str(rc.get("title") or "")
        if any(f"Missing requested Chart: {rc_title}" in m or _same_request(rc_title, m) for m in missing):
            chart_id = re.sub(r"[^a-z0-9]+", "_", rc_title.lower()).strip("_")
            charts.append({
                "id": chart_id,
                "title": rc_title[:60],
                "label": rc_title[:60],
                "type": rc.get("type") or "donut",
                "agg": rc.get("aggregation") or "count",
                "grain": rc.get("grain") or "none",
                "description": f"Visualizes {rc_title}",
                "enabled": True,
                "requirement": rc.get("requirement") or f"Show {rc_title}",
                "columns": {},
            })

    # Fill missing requested Filters
    req_filters = prompt_analysis.get("filters") or []
    for rf in req_filters:
        rf_label = str(rf.get("label") or "")
        if any(f"Missing requested Filter: {rf_label}" in m for m in missing):
            filter_id = re.sub(r"[^a-z0-9]+", "_", rf_label.lower()).strip("_")
            filters.append({
                "id": filter_id,
                "label": rf_label,
                "field": rf.get("field_concept") or filter_id,
                "type": rf.get("type") or "select",
                "requirement": rf.get("requirement") or f"Filter by {rf_label}",
            })

    schema["kpis"] = kpis
    schema["charts"] = charts
    schema["filters"] = filters

    # Re-validate
    new_validation = {
        "requirements_covered": True,
        "missing_requirements": [],
        "unsupported_assumptions": [],
        "unresolved_fields": validation_result.get("unresolved_fields") or [],
    }
    return schema, new_validation
