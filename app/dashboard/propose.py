"""Propose dynamic dashboard schema from user prompt + repository columns.

Follows the Core Principle: The user's prompt is the source of truth.
No hardcoded domain schemas or predefined dashboard templates.
Separation of Responsibilities:
Prompt Understanding -> Requirement Extraction -> Schema Generation ->
Data Requirement Generation -> Validation & Regeneration Loop.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

from app.dashboard.analyzer import analyze_prompt, analyze_prompt_heuristic, infer_aggregation
from app.dashboard.llm import LLMError, chat_json
from app.dashboard.pack import dashboard_system_prompt
from app.dashboard.tokens import (
    ACCENT_PRIMARY,
    CHART_PALETTE,
    ERROR_MAIN,
    INFO_MAIN,
    PRIMARY,
    SUCCESS_MAIN,
    WARNING_MAIN,
)
from app.dashboard.validator import (
    ALLOWED_AGGS,
    ALLOWED_CHART_TYPES,
    _same_request,
    regenerate_and_correct_schema,
    validate_schema,
)
from app.dashboard.widgets import (
    AMOUNT_ALIASES,
    CURRENCY_ALIASES,
    DUE_ALIASES,
    INVOICE_DATE_ALIASES,
    MATCH_ALIASES,
    PAID_ALIASES,
    STATUS_ALIASES,
    SUPPLIER_ALIASES,
    bind_columns,
    rebind_sparse_group_columns,
    repair_live_spec,
)

logger = logging.getLogger("orchestrator.dashboard.propose")

ALLOWED_TYPES = ALLOWED_CHART_TYPES

SKIP_GROUP = {
    "id", "tenantid", "repositoryid", "folderid", "ocrtext", "ocrjson", "summaryjson",
    "filepath", "storageproviderid", "workflowinstanceid",
}

AGG_SYNONYMS: dict[str, str] = {
    "sum": "sum",
    "total": "sum",
    "amount": "sum",
    "avg": "avg",
    "average": "avg",
    "mean": "avg",
    "count": "count",
    "records": "count",
    "num": "count",
    "distinct": "distinct",
    "unique": "distinct",
    "distinct_count": "distinct",
    "unique_count": "distinct",
    "min": "min",
    "minimum": "min",
    "lowest": "min",
    "max": "max",
    "maximum": "max",
    "highest": "max",
    "paid": "paid_sum",
    "paid_sum": "paid_sum",
    "outstanding": "outstanding_sum",
    "outstanding_sum": "outstanding_sum",
    "outstanding_count": "outstanding_count",
    "overdue": "overdue_sum",
    "overdue_sum": "overdue_sum",
    "overdue_count": "overdue_count",
    "current": "current_sum",
    "current_sum": "current_sum",
}

CHART_TYPE_SYNONYMS: dict[str, str] = {
    "bar": "bar",
    "barchart": "bar",
    "bar_chart": "bar",
    "horizontal_bar": "bar",
    "hbar": "bar",
    "column": "column",
    "columnchart": "column",
    "column_chart": "column",
    "vertical_bar": "column",
    "col": "column",
    "line": "line",
    "linechart": "line",
    "line_chart": "line",
    "donut": "donut",
    "donutchart": "donut",
    "donut_chart": "donut",
    "doughnut": "donut",
    "doughnutchart": "donut",
    "pie": "pie",
    "piechart": "pie",
    "pie_chart": "pie",
    "radar": "radar",
    "radarchart": "radar",
    "radar_chart": "radar",
    "area": "area",
    "areachart": "area",
    "area_chart": "area",
    "gauge": "gauge",
    "gaugechart": "gauge",
    "gauge_chart": "gauge",
    "lollipop": "lollipop",
    "lollipopchart": "lollipop",
    "lollipop_chart": "lollipop",
    "heatmap": "heatmap",
    "heat_map": "heatmap",
    "funnel": "funnel",
    "funnelchart": "funnel",
    "pipeline": "funnel",
    "table": "column",
    "details": "column",
    "matching": "lollipop",
}

_SYSTEM = """You are a dynamic Dashboard Reasoning and Generation Agent.
Your purpose is to generate a fully functional dashboard schema strictly from the user's prompt.

STRICT PRINCIPLES:
1. The user's prompt is the SOLE source of truth.
2. NO predefined dashboard content. Do NOT assume a specific business process unless provided in the prompt.
3. Every single component (KPI, chart, table, filter) MUST be traceable to the user's prompt via the 'requirement' field.
4. If the prompt specifies N KPIs or N charts (e.g. 5, 8, 10 KPIs), you MUST generate ALL of them in the 'kpis' and 'charts' arrays. NEVER drop, truncate, or summarize the user's requested metrics into a smaller sample.
5. Supported chart types: bar, column, line, area, pie, donut, funnel, gauge, radar, heatmap, lollipop.
6. If the user specifies layout (colors, top/bottom, 2-column), honor those requirements.
7. Generate data requirements dynamically for the fields required to build the dashboard.

Return JSON only in this exact contract:
{
  "prompt_analysis": {
    "domain": "Detected domain (e.g. Vessel Call, RFQ Lifecycle, HR, Operations, Sales, etc.)",
    "purpose": "Core purpose of the requested dashboard",
    "requirements": ["Summary of requirement 1", "Summary of requirement 2"]
  },
  "dashboard_schema": {
    "title": "Descriptive Dashboard Title",
    "description": "Short description of the dashboard",
    "purpose": "Dashboard objective",
    "kpis": [
      {
        "id": "snake_id",
        "title": "Title",
        "label": "Short Label",
        "description": "What this KPI measures",
        "type": "count|sum|avg|min|max|distinct",
        "data_field": "ColumnName or null",
        "aggregation": "count|sum|avg|min|max|distinct",
        "formatting": "number|currency|percent",
        "requirement": "Exact phrase/need from user prompt",
        "enabled": true,
        "order": 1,
        "position": "top"
      }
    ],
    "charts": [
      {
        "id": "snake_id",
        "title": "Chart Title",
        "description": "What this chart visualizes",
        "type": "donut|pie|radar|lollipop|column|bar|line|area|gauge|heatmap|funnel",
        "dimension": "ColumnName",
        "measure": "ColumnName or null",
        "aggregation": "count|sum|avg|min|max",
        "grain": "none|month|day|year",
        "requirement": "Exact phrase/need from user prompt",
        "enabled": true,
        "order": 1,
        "position": "left|right|full|top|bottom",
        "span": 1
      }
    ],
    "tables": [
      {
        "id": "snake_id",
        "title": "Table Title",
        "description": "Detailed record register",
        "columns": ["Col1", "Col2"],
        "requirement": "Exact phrase/need from user prompt"
      }
    ],
    "filters": [
      {
        "id": "snake_id",
        "label": "Filter Label",
        "field": "ColumnName",
        "type": "select|date|date_range|search|numeric_range",
        "requirement": "Exact phrase/need from user prompt"
      }
    ],
    "actions": [
      {
        "id": "export_csv",
        "label": "Export CSV",
        "type": "export",
        "requirement": "Data export"
      }
    ],
    "interactions": [
      {
        "id": "drill_down",
        "type": "drill_down",
        "source": "charts",
        "target": "table",
        "requirement": "Click to filter records"
      }
    ],
    "layout": {
      "theme": "light|dark",
      "structure": "responsive_grid|two_column",
      "kpi_position": "top|bottom"
    }
  },
  "data_requirements": {
    "fields": [
      {
        "name": "col_name",
        "role": "dimension|measure|identifier|date|filter",
        "reason": "Required for ..."
      }
    ],
    "dimensions": ["col_name"],
    "measures": ["col_name"],
    "filters": ["col_name"],
    "calculations": []
  },
  "validation": {
    "requirements_covered": true,
    "missing_requirements": [],
    "unsupported_assumptions": [],
    "unresolved_fields": []
  }
}
"""


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _col_map(columns: list[str]) -> dict[str, str]:
    return {_norm(name): name for name in columns if name}


def resolve_column(columns: list[str], name: Optional[str]) -> Optional[str]:
    """Dynamically resolves a requested field concept to available table columns."""
    if not name or not columns:
        return None
    raw = str(name).strip()
    if raw in columns:
        return raw

    by_norm = _col_map(columns)
    norm_name = _norm(raw)
    if norm_name in by_norm:
        return by_norm[norm_name]

    # Universal semantic alias groups across business domains
    alias_groups = (
        AMOUNT_ALIASES,
        DUE_ALIASES,
        SUPPLIER_ALIASES,
        MATCH_ALIASES,
        INVOICE_DATE_ALIASES,
        CURRENCY_ALIASES,
        STATUS_ALIASES,
        PAID_ALIASES,
        ("vessel", "vesselname", "ship", "shipname", "vessel_id"),
        ("port", "portname", "harbor"),
        ("berth", "berthname", "berthcode"),
        ("arrival", "arrivaldate", "arrival_time", "arrived_at", "eta"),
        ("departure", "departuredate", "departure_time", "departed_at", "etd"),
        ("rfq", "rfqid", "rfqnumber", "rfq_no", "quotenumber"),
        ("customer", "customername", "client", "clientname"),
        ("equipment", "equipmenttype", "truck", "trailer"),
        ("origin", "source", "fromcity", "pickup"),
        ("destination", "target", "tocity", "delivery"),
        ("employee", "employeename", "staff", "owner", "assignee"),
        ("product", "item", "sku", "partnumber", "description"),
        ("quantity", "qty", "volume", "units", "count", "stock", "onhand"),
        ("valuation", "unitprice", "price", "value", "amount"),
        ("warehouse", "warehousename", "location", "site"),
        ("category", "categoryname", "itemcategory"),
        ("delay", "delayminutes", "delayhours", "delaytime"),
        ("fuel", "fuellevel", "fuelqty"),
        ("speed", "velocity"),
        ("driver", "drivername", "operator"),
        ("vehicle", "vehicleid", "vehiclename"),
        ("stay", "portstay", "portstayhours", "stayhours", "stayduration", "portduration"),
        ("turnaround", "turnaroundtime", "turnaroundhours"),
        ("berthing", "berthingtime", "berthtime"),
        ("imo", "imonumber", "imo"),
        ("voyage", "voyagenumber", "voyageno", "voyageid"),
        ("terminal", "terminalname", "terminalcode"),
        ("eta", "estimatedarrival", "eta"),
        ("etd", "estimateddeparture", "etd"),
        ("document", "documentstatus", "docstatus", "documents"),
        ("reason", "delayreason", "delaycause", "cause", "delaycategory"),
        ("vesseltype", "vessel_type", "shiptype", "type"),
        ("ticket", "ticketid", "incident", "incidentid", "issue", "case", "casenumber"),
        ("priority", "severity", "urgency", "impact"),
        ("patient", "patientname", "patientid"),
        ("doctor", "physician", "provider"),
        ("department", "dept", "division", "team", "unit"),
        ("agent", "agentname", "representative", "handler"),
        ("rating", "score", "csat", "nps", "satisfaction"),
        ("revenue", "sales", "income", "turnover", "arr", "mrr"),
        ("cost", "expense", "spend", "expenditure"),
        ("profit", "margin", "net"),
        ("country", "region", "territory", "city", "state", "zone"),
        ("channel", "source", "medium", "campaign"),
        ("stage", "step", "phase", "pipeline_stage"),
        ("resolution", "resolutiontime", "handlingtime", "duration"),
        ("response", "responsetime", "firstresponse"),
        ("sla", "breach", "target"),
    )
    for group in alias_groups:
        if norm_name in group:
            for alias in group:
                if alias in by_norm:
                    return by_norm[alias]

    # Substring / containment match
    candidates = []
    for c in columns:
        c_norm = _norm(c)
        if norm_name and (norm_name in c_norm or c_norm in norm_name):
            candidates.append(c)
    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        candidates.sort(key=lambda c: abs(len(_norm(c)) - len(norm_name)))
        return candidates[0]

    # Fuzzy match as last resort
    close = difflib.get_close_matches(raw.lower(), [c.lower() for c in columns], n=1, cutoff=0.6)
    if close:
        for c in columns:
            if c.lower() == close[0]:
                return c

    return None


_GLUE_TOKENS = {
    "the", "and", "for", "with", "from", "that", "this", "show", "add", "into",
    "using", "allow", "filter", "filtering", "clicking", "detailed", "detail",
    "modern", "create", "build", "include", "requested", "overview", "wise",
    "based", "across", "their", "its", "per", "only", "just", "dashboard",
    "chart", "charts", "kpi", "kpis", "total", "number", "count", "average",
    "avg", "sum", "minimum", "maximum", "records",
}
_DATE_PARTS = ("date", "time", "timestamp", "arrival", "departure", "created", "eta", "etd")
_STATUS_PARTS = ("status", "state", "stage", "phase", "condition")
_MEASURE_PARTS = (
    "amount", "total", "qty", "quantity", "cost", "price", "value", "rate",
    "hour", "minute", "score", "valuation", "spend", "revenue", "fuel", "speed", "delay", "level",
)


def _concept_tokens(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z0-9]+", (text or "").lower())
        if word not in _GLUE_TOKENS and len(word) > 2
    ]


def _column_role(name: str) -> str:
    norm = _norm(name)
    if not norm or norm in SKIP_GROUP or norm.endswith("id"):
        return "identifier"
    if any(part in norm for part in _DATE_PARTS):
        return "date"
    if any(part in norm for part in _STATUS_PARTS):
        return "status"
    if any(part in norm for part in _MEASURE_PARTS):
        return "measure"
    return "category"


def _first_role(columns: list[str], role: str) -> Optional[str]:
    for column in columns:
        if _column_role(column) == role:
            return column
    return None


def _pick_column(columns: list[str], text: str, *, role: Optional[str] = None) -> Optional[str]:
    """Resolve a prompt phrase to one real column, preferring the requested role."""
    matches: list[str] = []
    for token in _concept_tokens(text):
        hit = resolve_column(columns, token)
        if hit and hit not in matches:
            matches.append(hit)
    if role == "date":
        for hit in matches:
            if _column_role(hit) == "date":
                return hit
        if re.search(r"\b(date|time|month|monthly|trend|timeline)\b", text or "", re.I):
            return _first_role(columns, "date")
        return None
    if role == "measure":
        for hit in matches:
            if _column_role(hit) == "measure":
                return hit
        return None
    if role == "status":
        for hit in matches:
            if _column_role(hit) == "status":
                return hit
        return _first_role(columns, "status")
    if role == "dimension":
        for hit in matches:
            if _column_role(hit) in {"category", "status", "date"}:
                return hit
        return None
    return matches[0] if matches else None


def _dimension_column(columns: list[str], title: str, dimension: str = "") -> Optional[str]:
    phrase = dimension if dimension and dimension != "category" else ""
    if not phrase:
        by_match = re.search(r"(?:by|per)\s+([a-z0-9 _-]+)", title or "", re.I)
        wise_match = re.search(r"([a-z0-9]+)-wise", title or "", re.I)
        if by_match:
            phrase = by_match.group(1)
        elif wise_match:
            phrase = wise_match.group(1)
    if phrase:
        hit = _pick_column(columns, phrase, role="dimension") or resolve_column(columns, phrase)
        if hit and _column_role(hit) != "identifier":
            return hit
    if re.search(r"\bstatus\b", f"{title} {dimension}", re.I):
        return _pick_column(columns, "status", role="status") or _first_role(columns, "status")
    return _pick_column(columns, title, role="dimension")


def _preferred_group(
    title: str,
    chart_type: str,
    grain: str,
    columns: list[str],
    *,
    dimension: str = "",
) -> Optional[str]:
    text = f"{title} {dimension}".lower()
    if grain in {"month", "day", "year", "week"} or chart_type == "line" or any(
        word in text for word in ("trend", "timeline", "over time")
    ):
        return _pick_column(columns, text, role="date") or _first_role(columns, "date")
    if chart_type == "funnel":
        return _first_role(columns, "status") or _dimension_column(columns, title, dimension)
    if re.search(r"\b(cause|reason|analysis)\b", text):
        reason = resolve_column(columns, "reason") or resolve_column(columns, "cause")
        if reason and _column_role(reason) != "identifier":
            return reason
    measure = _pick_column(columns, text, role="measure")
    if measure and re.search(r"\b(distribution|histogram)\b", text):
        return measure
    group = _dimension_column(columns, title, dimension)
    if group:
        return group
    if chart_type in {"donut", "pie", "funnel"}:
        return _first_role(columns, "status") or _first_role(columns, "category")
    return _first_role(columns, "category") or _first_role(columns, "status")


_STATUS_PHRASES = (
    ("currently in port", "In Port"),
    ("in port", "In Port"),
    ("in progress", "In Progress"),
    ("ready for departure", "Ready for Departure"),
    ("scheduled", "Scheduled"),
    ("arrived", "Arrived"),
    ("arriving", "Arriving"),
    ("departed", "Departed"),
    ("completed", "Completed"),
    ("complete", "Completed"),
    ("cancelled", "Cancelled"),
    ("canceled", "Cancelled"),
    ("delayed", "Delayed"),
    ("active", "Active"),
    ("won", "Won"),
    ("lost", "Lost"),
    ("pending", "Pending"),
    ("closed", "Closed"),
    ("open", "Open"),
)


def _attach_status_scope(
    kpi: dict[str, Any],
    cols: dict[str, str],
    columns: list[str],
    label: str,
) -> None:
    """Scope a count KPI to the status or stock condition named in the prompt."""
    text = label.lower()
    if "out of stock" in text:
        qty = _pick_column(columns, "quantity stock", role="measure") or _first_role(columns, "measure")
        if qty:
            cols["value"] = qty
            kpi["where"] = {"column": qty, "op": "eq", "value": 0}
        return
    if "document" in text:
        document_col = resolve_column(columns, "document") or resolve_column(columns, "document status")
        if document_col and re.search(r"\b(pending|missing|rejected|processed)\b", text):
            word = re.search(r"\b(pending|missing|rejected|processed)\b", text).group(1)
            cols["status"] = document_col
            cols["equals"] = word.capitalize()
        return
    if re.match(r"^total\b", text):
        return
    status_col = _first_role(columns, "status")
    if not status_col or cols.get("equals"):
        return
    for phrase, value in _STATUS_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            cols["status"] = status_col
            cols["equals"] = value
            return


def _bind_widget_columns(
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    columns: list[str],
) -> None:
    """Fill widget fields from the prompt text when the model left them unresolved."""
    for kpi in kpis:
        cols = kpi.get("columns") if isinstance(kpi.get("columns"), dict) else {}
        label = str(kpi.get("label") or kpi.get("title") or "")
        if cols.get("value") and str(kpi.get("agg") or "") in {"sum", "avg", "min", "max", "distinct"}:
            kpi["columns"] = cols
            continue
        stated = str(kpi.get("agg") or "")
        inferred = infer_aggregation(label, stated)
        if stated == "sum" and inferred == "count":
            kpi["agg"] = "count"
            kpi["type"] = "count"
            kpi["aggregation"] = "count"
            inferred = "count"
        agg = str(kpi.get("agg") or inferred or "count")
        if agg in {"sum", "avg", "min", "max"}:
            value = _pick_column(columns, label, role="measure")
            if value:
                cols["value"] = value
                kpi["data_field"] = value
        elif agg == "distinct":
            value = (
                _pick_column(columns, label, role="dimension")
                or _first_role(columns, "category")
                or _first_role(columns, "identifier")
            )
            if value:
                cols["value"] = value
                kpi["data_field"] = value
        if agg in {"count", "distinct"} and not kpi.get("where") and not cols.get("equals"):
            _attach_status_scope(kpi, cols, columns, label)
        kpi["columns"] = cols

    for chart in charts:
        cols = chart.get("columns") if isinstance(chart.get("columns"), dict) else {}
        title = str(chart.get("title") or chart.get("label") or "")
        chart_type = str(chart.get("type") or "donut")
        grain = str(chart.get("grain") or "none")
        group = cols.get("group")
        if not group or _column_role(group) == "identifier" or group not in columns:
            group = _preferred_group(title, chart_type, grain, columns, dimension=str(chart.get("dimension") or ""))
        if group:
            cols["group"] = group
            chart["dimension"] = group
        reason_group = bool(re.search(r"reason|cause", str(group or ""), re.I))
        if not cols.get("value") and not reason_group:
            measure = _pick_column(columns, title, role="measure")
            if measure and measure != group and grain == "none" and chart_type not in {"line", "area"}:
                if not re.search(r"\b(distribution|breakdown|mix|status)\b", title, re.I):
                    cols["value"] = measure
                    chart["measure"] = measure
                    if str(chart.get("agg") or "count") == "count":
                        chart["agg"] = "sum"
                        chart["aggregation"] = "sum"
        chart["columns"] = cols

    for filt in filters:
        if not isinstance(filt, dict):
            continue
        label = str(filt.get("label") or "")
        concept = str(filt.get("field") or filt.get("field_concept") or label)
        current = resolve_column(columns, concept)
        text = f"{label} {concept}"
        if re.search(r"\b(date|time|period)\b", text, re.I):
            current = _pick_column(columns, text, role="date") or _first_role(columns, "date") or current
            filt["type"] = filt.get("type") or "date"
        elif re.search(r"\bstatus\b", text, re.I):
            current = (
                _pick_column(columns, text, role="status")
                or _pick_column(columns, "status", role="status")
                or current
            )
        elif not current:
            current = _pick_column(columns, text, role="dimension") or resolve_column(columns, label)
        if current:
            filt["field"] = current
        filt.setdefault("id", _slug(label or concept, "filter"))
        filt.setdefault("requirement", f"Filter by {label or concept}")


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40]
    return slug or fallback


NAMED_COLORS = {
    "red": ERROR_MAIN,
    "blue": INFO_MAIN,
    "green": SUCCESS_MAIN,
    "cyan": PRIMARY,
    "teal": "var(--teal-9, #12a594)",
    "purple": ACCENT_PRIMARY,
    "violet": ACCENT_PRIMARY,
    "orange": WARNING_MAIN,
    "pink": "var(--pink-9, #d6409f)",
    "yellow": "var(--yellow-9, #ffc53d)",
    "black": "var(--text-primary, var(--gray-13, #1f2937))",
    "navy": "var(--indigo-9, #3e63dd)",
    "indigo": "var(--indigo-9, #3e63dd)",
    "gold": "var(--gold-9, #978365)",
}
KPI_COLORS = list(CHART_PALETTE)
POSITIONS = {"top", "left", "right", "bottom", "full", "center"}

_KPI_ONLY_RE = re.compile(
    r"\b(only\s+kpis?|kpis?\s+only|just\s+kpis?|no\s+charts?|without\s+charts?)\b",
    re.I,
)


def wants_kpis_only(message: str) -> bool:
    """True when user requested only KPI cards (no charts)."""
    return bool(_KPI_ONLY_RE.search(message or ""))


def apply_ask_limits(
    message: str, kpis: list[dict[str, Any]], charts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    msg = (message or "").strip()
    if wants_kpis_only(msg):
        charts = []

    # Check for explicit KPI count in prompt (e.g. "10 KPI cards", "10 KPIs", "only 4 KPIs", "show 10 KPIs")
    kpi_match = re.search(r"\b(?:only|exactly|limit\s+to|show|include|with|have)?\s*(\d{1,3})\s*(?:kpi\s*cards?|kpis?|metrics?)\b", msg, re.I)
    if kpi_match:
        try:
            target_kpis = int(kpi_match.group(1))
            if 0 < target_kpis < len(kpis):
                kpis = kpis[:target_kpis]
        except (ValueError, TypeError):
            pass

    # Check for explicit Chart count in prompt (e.g. "10 charts", "10 sections", "only 5 charts")
    chart_match = re.search(r"\b(?:only|exactly|limit\s+to|show|include|with|have)?\s*(\d{1,3})\s*(?:charts?|visualizations?|graphs?|dashboard\s+sections?|sections?)\b", msg, re.I)
    if chart_match:
        try:
            target_charts = int(chart_match.group(1))
            if 0 < target_charts < len(charts):
                charts = charts[:target_charts]
        except (ValueError, TypeError):
            pass

    return kpis, charts


_SINGULAR_KPI_ASK = re.compile(
    r"\b(only|just)\s+(the\s+)?(single|one|specific)\s+kpi\b",
    re.I,
)
_GENERIC_DASHBOARD_ASKS = {
    "", "dashboard", "dashboards", "overview", "i need a dashboard",
    "i need a dashboard.", "build a dashboard", "create a dashboard",
    "general dashboard", "full dashboard", "summary dashboard",
}
_SPECIFIC_COUNT_RE = re.compile(
    r"\b(only\s+\d+|\d+\s+kpis?|\d+\s+metrics?|one\s+kpi|two\s+kpis?|three\s+kpis?|four\s+kpis?|single\s+kpi)\b",
    re.I,
)


def _should_enrich_kpis(message: str, kpis: list[dict[str, Any]]) -> bool:
    """Pad thin proposals only for broad/overview requests without specific metric constraints."""
    if len(kpis) >= 4:
        return False
    msg = (message or "").strip().lower()
    if _SINGULAR_KPI_ASK.search(msg) or _SPECIFIC_COUNT_RE.search(msg):
        return False
    if len(kpis) >= 2 and msg not in _GENERIC_DASHBOARD_ASKS and not any(kw in msg for kw in ("overview", "general", "full", "all")):
        return False
    return True


def enrich_sparse_kpis(
    message: str,
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    columns: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """If the model returned 1–2 KPIs for a broad ask, fill from dynamic generic pack."""
    if not _should_enrich_kpis(message, kpis):
        return kpis, charts
    fallback_kpis, _fallback_charts = propose_generic(columns, message=message)
    seen = {str(item.get("id") or "") for item in kpis}
    for item in fallback_kpis:
        widget_id = str(item.get("id") or "")
        if not widget_id or widget_id in seen:
            continue
        kpis.append(item)
        seen.add(widget_id)
        if len(kpis) >= 6:
            break
    apply_default_layout(kpis, charts)
    return kpis, charts


def parse_color(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]
    if text.startswith("#"):
        hex_part = text[1:]
    else:
        hex_part = text
    if re.fullmatch(r"[0-9a-f]{3}", hex_part) or re.fullmatch(r"[0-9a-f]{6}", hex_part):
        return "#" + hex_part
    return None


def _layout_from(raw: dict[str, Any], index: int, *, kind: str) -> dict[str, Any]:
    try:
        order = int(raw.get("order"))
    except (TypeError, ValueError):
        order = index + 1
    pos = str(raw.get("position") or raw.get("place") or "").strip().lower()
    if pos not in POSITIONS:
        pos = "top" if kind == "kpi" else "full"
    color = parse_color(raw.get("color") or raw.get("colour"))
    try:
        span = int(raw.get("span"))
    except (TypeError, ValueError):
        span = 1 if pos in {"left", "right"} else 2
    if kind == "chart" and pos in {"left", "right"}:
        span = 1
    if kind == "chart" and pos in {"full", "bottom", "top"}:
        span = 2
    out: dict[str, Any] = {"order": max(1, order), "position": pos}
    if color:
        out["color"] = color
    if kind == "chart":
        out["span"] = 1 if span == 1 else 2
    return out


def unpack_proposal(result: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Accepts either (kpis, charts), or full dashboard schema dict."""
    if isinstance(result, dict):
        if "dashboard_schema" in result and isinstance(result["dashboard_schema"], dict):
            ds = result["dashboard_schema"]
            return ds.get("kpis") or [], ds.get("charts") or []
        kpis = result.get("kpis") if isinstance(result.get("kpis"), list) else []
        charts = result.get("charts") if isinstance(result.get("charts"), list) else []
        return kpis, charts
    if isinstance(result, (tuple, list)) and len(result) >= 2:
        return list(result[0] or []), list(result[1] or [])
    return [], []


def _text_description(raw: dict[str, Any], fallback: str) -> str:
    text = str(raw.get("description") or raw.get("subtitle") or "").strip()
    return (text or fallback)[:180]


def _fallback_kpi_description(label: str, agg: str, cols: dict[str, str]) -> str:
    value = cols.get("value")
    if agg == "count":
        return f"Total count of {label.lower()} in the dataset."
    if agg == "sum" and value:
        return f"Sum of {value} across records."
    if agg == "avg" and value:
        return f"Average {value} across records."
    if agg == "distinct" and value:
        return f"Number of distinct {value} entries."
    if agg == "min" and value:
        return f"Minimum {value} recorded."
    if agg == "max" and value:
        return f"Maximum {value} recorded."
    return f"Calculated metric for {label}."


def _fallback_chart_description(
    title: str, chart_type: str, cols: dict[str, str], agg: str, grain: str
) -> str:
    group = cols.get("group") or "category"
    value = cols.get("value")
    if grain == "month":
        return f"Monthly distribution of {title.lower()} over time."
    if value and agg != "count":
        return f"{agg.title()} of {value} grouped by {group}."
    return f"Count of records grouped by {group}."


def apply_default_layout(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    for i, item in enumerate(kpis):
        item.setdefault("order", i + 1)
        item.setdefault("position", "top")
        item.setdefault("color", KPI_COLORS[i % len(KPI_COLORS)])
    for i, item in enumerate(charts):
        item.setdefault("order", i + 1)
        item.setdefault("position", "left" if i % 2 == 0 else "right")
        item.setdefault("span", 1)


def overlay_layout_from_message(
    message: str, kpis: list[dict[str, Any]], charts: list[dict[str, Any]]
) -> None:
    msg = (message or "").lower()
    if not msg:
        return
    if "kpi at bottom" in msg or "kpis on bottom" in msg or "charts on top" in msg:
        for k in kpis:
            k["position"] = "bottom"
        for c in charts:
            c["position"] = "top"
    for chart_type in ("bar", "column", "donut", "pie", "line", "area", "radar", "gauge", "heatmap", "funnel"):
        if f"{chart_type} chart" in msg or f"a {chart_type}" in msg:
            if charts:
                charts[0]["type"] = chart_type
                break


def ensure_widget_descriptions(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    for k in kpis:
        if not str(k.get("description") or "").strip():
            k["description"] = f"Monitors {k.get('label') or 'metric'}."
    for c in charts:
        if not str(c.get("description") or "").strip():
            c["description"] = f"Visualizes {c.get('title') or 'distribution'}."


def _occupancy(columns: list[str], sample_rows: list[dict[str, Any]]) -> dict[str, float]:
    if not sample_rows:
        return {}
    out: dict[str, float] = {}
    for column in columns[:40]:
        filled = 0
        for row in sample_rows:
            if isinstance(row, dict) and row.get(column) is not None:
                filled += 1
        out[column] = round(100.0 * filled / len(sample_rows), 1)
    return out


def _normalize_kpis(items: Any, columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("name") or raw.get("label") or raw.get("title") or raw.get("metric") or raw.get("id") or "KPI").strip()
        widget_id = _slug(str(raw.get("id") or label), f"kpi_{len(out)+1}")
        if widget_id in seen:
            continue
        raw_agg = str(raw.get("agg") or raw.get("aggregation") or "count").lower()
        agg = AGG_SYNONYMS.get(raw_agg, raw_agg)
        if agg not in ALLOWED_AGGS:
            agg = infer_aggregation(label)
        existing = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}
        column = resolve_column(
            columns,
            raw.get("column") or raw.get("data_field") or raw.get("value") or existing.get("value") or raw.get("metric") or label,
        )
        cols: dict[str, str] = dict(existing)
        if column and agg in {"sum", "avg", "min", "max", "distinct"}:
            cols["value"] = column
        seen.add(widget_id)
        item = {
            "id": widget_id,
            "label": label[:40],
            "title": label[:40],
            "description": _text_description(raw, _fallback_kpi_description(label, agg, cols)),
            "type": agg,
            "agg": agg,
            "data_field": column,
            "aggregation": agg,
            "enabled": raw.get("enabled", True) is not False,
            "requirement": raw.get("requirement") or f"Monitor {label}",
            "columns": cols,
        }
        item.update(_layout_from(raw, len(out), kind="kpi"))
        out.append(item)
    return out


def _normalize_charts(items: Any, columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("label") or raw.get("name") or raw.get("id") or "Chart").strip()
        raw_type = str(raw.get("type") or "donut").lower()
        if raw_type in {"insight", "insights", "table"} and "chart" not in str(raw.get("type") or "").lower():
            continue
        if title.lower().strip() in {"insights", "key insights", "ai insights", "table", "records"} and raw_type not in ALLOWED_CHART_TYPES:
            continue
        widget_id = _slug(str(raw.get("id") or title), f"chart_{len(out)+1}")
        if widget_id in seen:
            continue
        raw_type = str(raw.get("type") or "donut").lower()
        chart_type = CHART_TYPE_SYNONYMS.get(raw_type, raw_type)
        if chart_type not in ALLOWED_CHART_TYPES:
            chart_type = "donut"
        grain = str(raw.get("grain") or "none").lower()
        if grain not in {"month", "none", "day", "year", "week"}:
            grain = "none"
        existing = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}
        group = resolve_column(columns, existing.get("group") or raw.get("group"))
        hinted = str(raw.get("dimension") or "")
        if (not group or _column_role(group) == "identifier") and hinted and hinted != "category":
            group = resolve_column(columns, hinted) or group
        if not group or _norm(group) in SKIP_GROUP or _column_role(group) == "identifier":
            group = _preferred_group(
                title,
                chart_type,
                grain,
                columns,
                dimension="" if hinted == "category" else hinted,
            )
        value = resolve_column(
            columns,
            existing.get("value") or raw.get("value") or raw.get("measure") or raw.get("column") or raw.get("metric"),
        )
        raw_agg = str(raw.get("agg") or raw.get("aggregation") or ("sum" if value else "count")).lower()
        agg = AGG_SYNONYMS.get(raw_agg, raw_agg)
        if agg not in {"count", "sum", "avg", "min", "max", "distinct"}:
            agg = "sum" if value else "count"
        seen.add(widget_id)
        cols = dict(existing)
        if group:
            cols["group"] = group
        if value and agg in {"sum", "avg", "min", "max"}:
            cols["value"] = value
        item = {
            "id": widget_id,
            "label": title[:60],
            "title": title[:60],
            "description": _text_description(
                raw, _fallback_chart_description(title, chart_type, cols, agg, grain)
            ),
            "type": chart_type,
            "dimension": group,
            "measure": value,
            "agg": agg,
            "aggregation": agg,
            "grain": grain,
            "enabled": raw.get("enabled", True) is not False,
            "requirement": raw.get("requirement") or f"Visualize {title}",
            "columns": cols,
        }
        item.update(_layout_from(raw, len(out), kind="chart"))
        out.append(item)
    return out


def propose_generic(
    columns: list[str],
    *,
    message: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generates dynamic dashboard components strictly from columns and message without domain templates."""
    heuristic = analyze_prompt_heuristic(message, {}, columns)
    kpis: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []

    # If heuristic extracted prompt-specific requirements, map them directly
    if heuristic.get("kpis"):
        for req_kpi in heuristic["kpis"]:
            rname = req_kpi["name"]
            agg = req_kpi.get("aggregation", "count")
            kpis.append({
                "id": _slug(rname, f"kpi_{len(kpis)+1}"),
                "label": rname[:40],
                "title": rname[:40],
                "description": f"Measures {rname}.",
                "type": agg,
                "agg": agg,
                "enabled": True,
                "requirement": req_kpi.get("requirement", f"Prompt requirement {rname}"),
                "columns": {},
            })

    if heuristic.get("charts"):
        for req_chart in heuristic["charts"]:
            ctitle = req_chart["title"]
            ctype = req_chart.get("type", "donut")
            grain = req_chart.get("grain", "none")
            dimension = str(req_chart.get("dimension") or "")
            group = _preferred_group(ctitle, ctype, grain, columns, dimension=dimension)
            charts.append({
                "id": _slug(ctitle, f"chart_{len(charts)+1}"),
                "title": ctitle[:60],
                "label": ctitle[:60],
                "type": ctype,
                "grain": grain,
                "dimension": dimension,
                "description": f"Visualizes {ctitle}.",
                "enabled": True,
                "requirement": req_chart.get("requirement", f"Prompt visualization {ctitle}"),
                "columns": {"group": group} if group else {},
            })

    # If no prompt-specific components, generate dynamic widgets from columns
    col_map = _col_map(columns)
    numeric_cols = [c for c in columns if any(kw in _norm(c) for kw in ("amount", "total", "qty", "quantity", "cost", "price", "count", "score", "value", "rate", "hours"))]
    status_cols = [c for c in columns if any(kw in _norm(c) for kw in ("status", "state", "stage", "phase", "condition"))]
    date_cols = [c for c in columns if any(kw in _norm(c) for kw in ("date", "time", "created", "arrival", "departure", "timestamp"))]
    cat_cols = [c for c in columns if _norm(c) not in SKIP_GROUP and c not in numeric_cols and c not in date_cols]

    if not kpis:
        # Generate generic KPIs from columns
        kpis.append({
            "id": "total_records",
            "label": "TOTAL RECORDS",
            "title": "Total Records",
            "type": "count",
            "agg": "count",
            "enabled": True,
            "requirement": "Overview record count",
            "columns": {},
        })
        if numeric_cols:
            kpis.append({
                "id": _slug(f"total_{numeric_cols[0]}", "total_val"),
                "label": f"TOTAL {numeric_cols[0].upper()}"[:40],
                "title": f"Total {numeric_cols[0]}",
                "type": "sum",
                "agg": "sum",
                "data_field": numeric_cols[0],
                "enabled": True,
                "requirement": f"Measure sum of {numeric_cols[0]}",
                "columns": {"value": numeric_cols[0]},
            })
            kpis.append({
                "id": _slug(f"avg_{numeric_cols[0]}", "avg_val"),
                "label": f"AVERAGE {numeric_cols[0].upper()}"[:40],
                "title": f"Average {numeric_cols[0]}",
                "type": "avg",
                "agg": "avg",
                "data_field": numeric_cols[0],
                "enabled": True,
                "requirement": f"Measure average of {numeric_cols[0]}",
                "columns": {"value": numeric_cols[0]},
            })
        if status_cols:
            kpis.append({
                "id": "active_records",
                "label": "ACTIVE STATUS",
                "title": "Active Status",
                "type": "count",
                "agg": "count",
                "enabled": True,
                "requirement": f"Track records by {status_cols[0]}",
                "columns": {"group": status_cols[0]},
            })

    if not charts:
        if status_cols:
            charts.append({
                "id": "status_distribution",
                "title": f"{status_cols[0].replace('_', ' ').title()} Breakdown",
                "label": f"{status_cols[0].replace('_', ' ').title()} Breakdown",
                "type": "donut",
                "enabled": True,
                "requirement": f"Status breakdown by {status_cols[0]}",
                "columns": {"group": status_cols[0]},
            })
        if cat_cols:
            dim = cat_cols[0]
            val = numeric_cols[0] if numeric_cols else None
            charts.append({
                "id": _slug(f"by_{dim}", "cat_chart"),
                "title": f"Distribution by {dim.replace('_', ' ').title()}",
                "label": f"Distribution by {dim.replace('_', ' ').title()}",
                "type": "bar",
                "enabled": True,
                "requirement": f"Breakdown by {dim}",
                "columns": {"group": dim, **({"value": val} if val else {})},
            })
        if date_cols:
            charts.append({
                "id": "trend_over_time",
                "title": "Trend Over Time",
                "label": "Trend Over Time",
                "type": "line",
                "grain": "month",
                "enabled": True,
                "requirement": f"Timeline trend across {date_cols[0]}",
                "columns": {"group": date_cols[0]},
            })

    _bind_widget_columns(kpis, charts, [], columns)
    apply_default_layout(kpis, charts)
    overlay_layout_from_message(message, kpis, charts)
    ensure_widget_descriptions(kpis, charts)
    repair_live_spec(kpis, charts, columns)
    return apply_ask_limits(message, kpis, charts)


def _merge_named(primary: Any, extra: Any, label_key: str = "title") -> list[dict[str, Any]]:
    merged = [item for item in (primary or []) if isinstance(item, dict)]
    seen = {_norm(str(item.get(label_key) or item.get("label") or "")) for item in merged}
    for item in extra or []:
        if not isinstance(item, dict):
            continue
        key = _norm(str(item.get(label_key) or item.get("label") or ""))
        if not key or any(key in existing or existing in key for existing in seen if existing):
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _resolve_tables(tables: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    """Keep only repository columns. Requested names that do not exist stay unresolved."""
    resolved_tables: list[dict[str, Any]] = []
    business = [column for column in columns if _column_role(column) != "identifier"]
    for raw in tables:
        requested = [str(name) for name in (raw.get("columns") or []) if str(name).strip()]
        mapped: list[str] = []
        missing: list[str] = []
        for name in requested:
            hit = resolve_column(columns, name)
            if hit and hit not in mapped:
                mapped.append(hit)
            elif not hit:
                missing.append(name)
        if not mapped:
            mapped = business[:12]
        item = {
            "id": _slug(str(raw.get("id") or raw.get("title") or "table"), f"table_{len(resolved_tables)+1}"),
            "title": str(raw.get("title") or "Records")[:80],
            "columns": mapped,
            "requirement": raw.get("requirement") or "Record table",
        }
        if missing:
            item["unresolved_columns"] = missing
        resolved_tables.append(item)
    return resolved_tables


def _prepare_filters(items: Any, columns: list[str]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return filters
    for raw in items:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("field") or raw.get("field_concept") or "Filter").strip()
        filters.append({
            "id": _slug(str(raw.get("id") or label), f"filter_{len(filters)+1}"),
            "label": label,
            "field": raw.get("field") or raw.get("field_concept") or label,
            "field_concept": raw.get("field_concept") or raw.get("field") or label,
            "type": raw.get("type") or "select",
            "requirement": raw.get("requirement") or f"Filter by {label}",
        })
    _bind_widget_columns([], [], filters, columns)
    return filters


def _data_requirements_from_schema(
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe only the columns the prompt-driven schema actually uses."""
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    dimensions: list[str] = []
    measures: list[str] = []
    filter_names: list[str] = []

    def add(name: Any, role: str, reason: str) -> None:
        column = str(name or "").strip()
        if not column or column in seen:
            return
        seen.add(column)
        fields.append({"name": column, "role": role, "reason": reason})
        if role == "measure":
            measures.append(column)
        elif role == "filter":
            filter_names.append(column)
        else:
            dimensions.append(column)

    for kpi in kpis:
        cols = kpi.get("columns") if isinstance(kpi.get("columns"), dict) else {}
        value_role = "measure" if str(kpi.get("agg") or "") in {"sum", "avg", "min", "max"} else "dimension"
        add(cols.get("value"), value_role, str(kpi.get("label") or "KPI"))
    for chart in charts:
        cols = chart.get("columns") if isinstance(chart.get("columns"), dict) else {}
        add(cols.get("group"), "dimension", str(chart.get("title") or "Chart"))
        add(cols.get("value"), "measure", str(chart.get("title") or "Chart"))
    for filt in filters:
        if isinstance(filt, dict):
            add(filt.get("field"), "filter", str(filt.get("label") or "Filter"))
    for table in tables or []:
        if isinstance(table, dict):
            for name in table.get("unresolved_columns") or []:
                add(name, "unresolved", str(table.get("title") or "Table"))
    return {
        "fields": fields,
        "dimensions": dimensions,
        "measures": measures,
        "filters": filter_names,
        "calculations": [],
    }


async def propose_dashboard(
    *,
    message: str,
    target: dict[str, Any],
    columns: list[str],
    sample_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generates dynamic dashboard contract conforming strictly to prompt specifications."""
    # Step 1: Prompt understanding & requirement extraction
    prompt_analysis = await analyze_prompt(
        message=message,
        target=target,
        columns=columns,
        sample_rows=sample_rows,
    )
    if not prompt_analysis.get("requirements"):
        prompt_analysis["requirements"] = list(prompt_analysis.get("requirements_summary") or [])

    # Step 2: Prompt-derived schema used when the model is unavailable or incomplete
    fb_kpis, fb_charts = propose_generic(columns, message=message)

    schema_payload: dict[str, Any] = {}
    try:
        user_content = (
            f"User Prompt:\n{message or 'Generate dynamic operational dashboard.'}\n\n"
            f"Prompt Analysis:\n{prompt_analysis}\n\n"
            f"Table: {target.get('qualified_table')}\n"
            f"Columns: {columns}\n"
            f"Sample Rows ({len(sample_rows or [])}): {(sample_rows or [])[:5]}\n"
        )
        sys_prompt = await dashboard_system_prompt("schema", _SYSTEM)
        response_json = await chat_json(
            system=sys_prompt,
            user=user_content,
            temperature=0.1,
        )
        if isinstance(response_json, dict):
            schema_payload = response_json
    except (LLMError, Exception) as exc:
        logger.info("propose_dashboard LLM failed or not configured (%s), using dynamic synthesizer", exc)

    # Convert extracted prompt analysis components if present
    kpis_from_prompt = []
    for rk in prompt_analysis.get("kpis") or []:
        r_name = str(rk.get("name") or rk.get("metric") or "").strip()
        if r_name:
            kpi_id = re.sub(r"[^a-z0-9]+", "_", r_name.lower()).strip("_") or "kpi"
            kpis_from_prompt.append({
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

    charts_from_prompt = []
    for rc in prompt_analysis.get("charts") or []:
        rc_title = str(rc.get("title") or "").strip()
        if rc_title:
            chart_id = re.sub(r"[^a-z0-9]+", "_", rc_title.lower()).strip("_") or "chart"
            charts_from_prompt.append({
                "id": chart_id,
                "title": rc_title[:60],
                "label": rc_title[:60],
                "type": rc.get("type") or "donut",
                "agg": rc.get("aggregation") or "count",
                "grain": rc.get("grain") or "none",
                "dimension": rc.get("dimension") or "",
                "measure": rc.get("measure") or "",
                "description": f"Visualizes {rc_title}",
                "enabled": True,
                "requirement": rc.get("requirement") or f"Show {rc_title}",
                "columns": {},
            })

    # Unpack or synthesize schema
    if schema_payload.get("dashboard_schema") and isinstance(schema_payload["dashboard_schema"], dict):
        raw_schema = dict(schema_payload["dashboard_schema"])
    elif schema_payload.get("kpis") or schema_payload.get("charts"):
        raw_schema = dict(schema_payload)
    else:
        # Build from heuristic / fallback
        raw_schema = {
            "title": prompt_analysis["title"],
            "description": prompt_analysis["purpose"],
            "purpose": prompt_analysis["purpose"],
            "kpis": kpis_from_prompt or fb_kpis,
            "charts": charts_from_prompt or fb_charts,
            "tables": prompt_analysis.get("tables") or [{
                "id": "record_table",
                "title": f"Detailed {prompt_analysis['domain']} Records",
                "columns": columns[:7],
                "requirement": "Record inspection table",
            }],
            "filters": prompt_analysis.get("filters") or [],
            "actions": [{"id": "export_csv", "label": "Export CSV", "type": "export", "requirement": "Data export"}],
            "interactions": prompt_analysis.get("interactions") or [],
            "layout": prompt_analysis.get("layout") or {},
        }

    # Ensure all explicitly requested KPIs from the user prompt are preserved
    if kpis_from_prompt:
        if raw_schema.get("kpis"):
            merged_kpis = []
            for rk in kpis_from_prompt:
                rk_title = str(rk.get("label") or rk.get("title") or "")
                llm_match = next((k for k in raw_schema["kpis"] if _same_request(rk_title, str(k.get("label") or k.get("title") or k.get("id") or ""))), None)
                if llm_match and isinstance(llm_match, dict):
                    combined = dict(llm_match)
                    combined["id"] = rk["id"]
                    combined["label"] = rk["label"]
                    combined["title"] = rk["title"]
                    merged_kpis.append(combined)
                else:
                    merged_kpis.append(rk)
            raw_schema["kpis"] = merged_kpis
        else:
            raw_schema["kpis"] = kpis_from_prompt

    # Ensure all explicitly requested Charts from the user prompt are preserved
    if charts_from_prompt:
        if raw_schema.get("charts"):
            merged_charts = []
            for rc in charts_from_prompt:
                rc_title = str(rc.get("title") or rc.get("label") or "")
                llm_match = next((c for c in raw_schema["charts"] if _same_request(rc_title, str(c.get("title") or c.get("label") or c.get("id") or ""))), None)
                if llm_match and isinstance(llm_match, dict):
                    combined = dict(llm_match)
                    combined["id"] = rc["id"]
                    combined["title"] = rc["title"]
                    combined["label"] = rc["title"]
                    if rc.get("type"):
                        combined["type"] = rc["type"]
                    merged_charts.append(combined)
                else:
                    merged_charts.append(rc)
            raw_schema["charts"] = merged_charts
        else:
            raw_schema["charts"] = charts_from_prompt

    def _assemble(source: dict[str, Any]) -> dict[str, Any]:
        kpis = _normalize_kpis(source.get("kpis"), columns)
        charts = _normalize_charts(source.get("charts"), columns)
        filters = _prepare_filters(
            _merge_named(source.get("filters"), prompt_analysis.get("filters"), "label"),
            columns,
        )
        tables = _resolve_tables(
            _merge_named(source.get("tables"), prompt_analysis.get("tables")),
            columns,
        )
        _bind_widget_columns(kpis, charts, filters, columns)
        apply_default_layout(kpis, charts)
        overlay_layout_from_message(message, kpis, charts)
        ensure_widget_descriptions(kpis, charts)
        repair_live_spec(kpis, charts, columns)
        rebind_sparse_group_columns(charts, sample_rows or [])
        kpis, charts = apply_ask_limits(message, kpis, charts)
        return {
            "title": str(source.get("title") or prompt_analysis.get("title") or "Dashboard"),
            "description": str(source.get("description") or prompt_analysis.get("purpose") or message),
            "purpose": str(source.get("purpose") or prompt_analysis.get("purpose") or message),
            "kpis": kpis,
            "charts": charts,
            "tables": tables,
            "filters": filters,
            "actions": list(source.get("actions") or [{"id": "export_csv", "label": "Export CSV", "type": "export"}]),
            "interactions": list(source.get("interactions") or prompt_analysis.get("interactions") or []),
            "layout": source.get("layout") if isinstance(source.get("layout"), dict) else (prompt_analysis.get("layout") or {}),
        }

    assembled_schema = _assemble(raw_schema)
    data_requirements = _data_requirements_from_schema(
        assembled_schema["kpis"],
        assembled_schema["charts"],
        assembled_schema["filters"],
        assembled_schema["tables"],
    )

    # Step 3: Schema validation against the prompt, then correct anything the model dropped
    val_result = validate_schema(
        prompt_analysis=prompt_analysis,
        schema=assembled_schema,
        data_requirements=data_requirements,
        columns=columns,
    )

    if not val_result["requirements_covered"]:
        assembled_schema, val_result = regenerate_and_correct_schema(
            prompt_analysis=prompt_analysis,
            schema=assembled_schema,
            validation_result=val_result,
            columns=columns,
        )
        assembled_schema = _assemble(assembled_schema)
        data_requirements = _data_requirements_from_schema(
            assembled_schema["kpis"],
            assembled_schema["charts"],
            assembled_schema["filters"],
            assembled_schema["tables"],
        )
        val_result = validate_schema(
            prompt_analysis=prompt_analysis,
            schema=assembled_schema,
            data_requirements=data_requirements,
            columns=columns,
        )

    return {
        "prompt_analysis": prompt_analysis,
        "dashboard_schema": assembled_schema,
        "data_requirements": data_requirements,
        "validation": val_result,
        # Direct backward compatibility exports
        "title": assembled_schema["title"],
        "description": assembled_schema["description"],
        "kpis": assembled_schema["kpis"],
        "charts": assembled_schema["charts"],
        "tables": assembled_schema["tables"],
        "filters": assembled_schema["filters"],
        "actions": assembled_schema["actions"],
        "interactions": assembled_schema["interactions"],
        "layout": assembled_schema["layout"],
    }
