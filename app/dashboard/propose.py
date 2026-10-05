"""Propose KPIs/charts from user message + repository columns (LLM, with generic fallback)."""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

from app.dashboard.llm import LLMError, chat_json
from app.dashboard.pack import dashboard_system_prompt
from app.dashboard.tokens import ACCENT_PRIMARY, CHART_PALETTE, ERROR_MAIN, INFO_MAIN, PRIMARY, SUCCESS_MAIN, WARNING_MAIN
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

logger = logging.getLogger("v6_dashboard.propose")

ALLOWED_TYPES = {"donut", "pie", "radar", "lollipop", "column", "bar", "line", "area", "gauge", "heatmap"}
ALLOWED_AGGS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "distinct",
    "overdue_sum",
    "overdue_count",
    "paid_sum",
    "outstanding_sum",
    "outstanding_count",
    "current_sum",
}
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
    "funnel": "bar",
    "pipeline": "column",
    "table": "column",
    "details": "column",
    "matching": "lollipop",
}

_SYSTEM = """You design one dashboard from the user request plus this EZOFIS items table.

The user request is the spec. You MUST faithfully implement ALL features, metrics, and chart requests in the prompt.
Different requests MUST produce different KPIs and charts tailored specifically to the prompt.
Do not omit features, metrics, groups, or charts explicitly requested by the user.
If the user specifies particular chart types (e.g. bar, column, line, pie, donut, area, radar, lollipop, gauge, heatmap), use those exact types.
Do not emit a canned Accounts Payable pack (total invoices + payable + paid + outstanding + overdue + the five standard AP charts) unless the user asked for a full AP overview.
If they asked only for KPIs (`only kpis`, `kpis only`, `just kpis`, `no charts`), return `"charts": []`. Do not add charts anyway.
If they asked for a specific number of KPIs (e.g. 2 KPIs, 10 KPIs), return that exact quantity.
If they asked only for risk, overdue, vendors, aging, match status, or files/HR, return only widgets that serve that ask.
Repository name is context, not an instruction. Do not assume Accounts Payable just because the repository is named that.

Money vs files:
- InvoiceAmount / Amount / InvoiceTotal are money. FileSize is bytes. Never use FileSize, FileType, or MimeType for payable, paid, outstanding, or overdue.
- InvoiceAmount may be empty in the sample rows and still be the correct money column (values can be filled later).
- Use FileType / FileSize only when the user asked about files, documents, or size.

Grouping:
- Group by a column with non-empty values in the occupancy list, or by columns explicitly requested by the user.
- If Status/AiStatus is empty and MatchedStatus has values, group invoice/match charts by MatchedStatus.
- Do not invent an Unknown bucket.

Aggregations you may use:
- KPIs: count, sum, avg, min, max, distinct, overdue_sum, overdue_count, paid_sum, outstanding_sum, current_sum
- Charts: count, sum, avg, min, max, outstanding_sum, paid_sum
- paid_sum / outstanding_sum need column=InvoiceAmount (or Amount) and match_column=MatchedStatus
  (Matched/Approved = paid; Not Matched/Partially Matched = outstanding).
- overdue_* need money column + date_column (DueDate) and match_column. Overdue is unpaid AND past due, so it cannot exceed outstanding.
- Never create KPIs named 1-30 / 31-60 / 61-90 / 90+ unless specifically asked. Aging chart is grain=aging, group=DueDate, value=InvoiceAmount.
- Payment status is grain=payment (Paid vs Outstanding amounts), never a second MatchedStatus count.

Layout: honor order, left/right/top/bottom/full, and colors from the user.
KPIs stay on top unless the user wants them at the bottom. If charts should be above KPIs, every KPI position=bottom.
If they do not mention layout, omit position/color/order.

Limits:
- For general overview asks without a specified widget count, return 4–6 KPIs and 2–5 charts.
- When the user asks for a specific list or number of KPI cards or charts/sections (e.g. 10 KPIs, 10 sections), generate an entry for EVERY single requested item. Do not truncate, cap at 8, or drop any requested item.
Every widget needs a one-sentence description with no numbers. Return JSON only.

{
  "kpis": [{"id":"snake_id","label":"SHORT LABEL","description":"What this KPI measures","agg":"count|sum|avg|min|max|distinct|overdue_sum|overdue_count|paid_sum|outstanding_sum","column":"ExactColumn or null","date_column":"ExactColumn or null","match_column":"ExactColumn or null","enabled":true,"order":1,"position":"top or bottom","color":"#7c5cff or red"}],
  "charts": [{"id":"snake_id","title":"Title","description":"What this chart shows","type":"donut|pie|radar|lollipop|column|bar|line|area|gauge|heatmap","group":"ExactColumn","value":"ExactColumn or null","agg":"count|sum|avg|min|max|outstanding_sum|paid_sum","grain":"none|month|aging|payment","match_column":"ExactColumn or null","enabled":true,"order":1,"position":"left|right|full|top","color":"#7c5cff or blue"}]
}
"""


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _col_map(columns: list[str]) -> dict[str, str]:
    return {_norm(name): name for name in columns if name}


def resolve_column(columns: list[str], name: Optional[str]) -> Optional[str]:
    if not name or not columns:
        return None
    raw = str(name).strip()
    if raw in columns:
        return raw

    by_norm = _col_map(columns)
    norm_name = _norm(raw)
    if norm_name in by_norm:
        return by_norm[norm_name]

    # Semantic alias groups
    alias_groups = (
        AMOUNT_ALIASES,
        DUE_ALIASES,
        SUPPLIER_ALIASES,
        MATCH_ALIASES,
        INVOICE_DATE_ALIASES,
        CURRENCY_ALIASES,
        STATUS_ALIASES,
        PAID_ALIASES,
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
    """True when the user asked for KPI cards and nothing else (no charts, no insights)."""
    return bool(_KPI_ONLY_RE.search(message or ""))


def apply_ask_limits(
    message: str, kpis: list[dict[str, Any]], charts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if wants_kpis_only(message):
        return kpis, []
    return kpis, charts


_SINGULAR_KPI_ASK = re.compile(
    r"\b(only|just)\s+(the\s+)?(overdue|risk|aging|vendor|supplier|match(?:ed)?|paid|outstanding|dpo)\b",
    re.I,
)
_GENERIC_DASHBOARD_ASKS = {
    "", "dashboard", "dashboards", "overview", "i need a dashboard",
    "i need a dashboard.", "build a dashboard", "create a dashboard",
    "build an ap dashboard", "i need an ap dashboard", "ap dashboard",
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
    """If the model returned 1–2 KPIs for a broad ask, fill from the generic pack."""
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
    """Accept (kpis, charts) from the proposer."""
    if isinstance(result, dict):
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
    date_col = cols.get("date")
    if agg == "count":
        return f"Count of records in this repository ({label})."
    if agg == "sum" and value:
        return f"Sum of {value} across matching records."
    if agg == "avg" and value:
        return f"Average {value} across matching records."
    if agg == "distinct" and value:
        return f"Number of distinct {value} values."
    if agg == "overdue_sum" and value:
        return f"Sum of {value} still unpaid where {date_col or 'due date'} is past today."
    if agg == "overdue_count":
        return f"Count of records past {date_col or 'due date'}."
    if agg == "paid_sum" and value:
        return f"Sum of {value} on invoices treated as paid or fully matched."
    if agg == "outstanding_sum" and value:
        return f"Sum of {value} on invoices that are not yet paid."
    if agg == "outstanding_count":
        return f"Count of invoices that are not yet paid."
    if agg == "current_sum" and value:
        return f"Sum of unpaid {value} that is not past due."
    return f"KPI for {label}."


def _fallback_chart_description(title: str, chart_type: str, cols: dict[str, str], agg: str, grain: str) -> str:
    group = cols.get("group")
    value = cols.get("value")
    kind = chart_type or "chart"
    if grain == "month":
        if value:
            return f"{kind.capitalize()} of {agg} {value} by month."
        return f"{kind.capitalize()} of record count by month."
    if grain == "aging":
        return f"{kind.capitalize()} of {value or 'amount'} by aging bucket."
    if grain == "payment":
        return f"{kind.capitalize()} of paid versus outstanding amounts."
    if value and group:
        return f"{kind.capitalize()} showing {agg} of {value} grouped by {group}."
    if group:
        return f"{kind.capitalize()} showing record count grouped by {group}."
    return f"{kind.capitalize()} for {title}."


def ensure_widget_descriptions(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    for kpi in kpis:
        if not str(kpi.get("description") or "").strip():
            kpi["description"] = _fallback_kpi_description(
                str(kpi.get("label") or kpi.get("id") or "KPI"),
                str(kpi.get("agg") or "count"),
                kpi.get("columns") if isinstance(kpi.get("columns"), dict) else {},
            )
    for chart in charts:
        if not str(chart.get("description") or "").strip():
            chart["description"] = _fallback_chart_description(
                str(chart.get("title") or chart.get("label") or chart.get("id") or "Chart"),
                str(chart.get("type") or "chart"),
                chart.get("columns") if isinstance(chart.get("columns"), dict) else {},
                str(chart.get("agg") or "count"),
                str(chart.get("grain") or "none"),
            )


def apply_default_layout(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    """Fill order/position/color when the user (or model) did not set them."""
    for index, kpi in enumerate(kpis):
        kpi.setdefault("order", index + 1)
        kpi.setdefault("position", "top")
        kpi.setdefault("color", KPI_COLORS[index % len(KPI_COLORS)])
    for index, chart in enumerate(charts):
        chart.setdefault("order", index + 1)
        if "position" not in chart:
            if len(charts) == 1:
                chart["position"] = "full"
            else:
                chart["position"] = "left" if index % 2 == 0 else "right"
        pos = chart.get("position")
        chart.setdefault("span", 1 if pos in {"left", "right"} else 2)
        if "color" not in chart:
            chart["color"] = KPI_COLORS[index % len(KPI_COLORS)]


def overlay_layout_from_message(message: str, kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    """Force charts-above-KPIs or user-specified chart types when the user said so in plain language."""
    blob = re.sub(r"\s+", " ", (message or "").lower())
    if not blob:
        return
    charts_first = any(
        phrase in blob
        for phrase in (
            "charts at the top",
            "charts on top",
            "charts on the top",
            "chart at the top",
            "chart on top",
            "chart at top",
            "charts at top",
            "kpis at the bottom",
            "kpis on the bottom",
            "kpis in the bottom",
            "kpis in bottom",
            "kpi at the bottom",
            "kpi on the bottom",
            "kpis below",
            "kpi below",
        )
    )
    if charts_first:
        for item in kpis:
            item["position"] = "bottom"
        for item in charts:
            if item.get("position") not in {"left", "right"}:
                item["position"] = "top"
                item["span"] = 2

    # Check for user-specified chart types in the prompt
    chart_type_hints = [
        ("bar", ("bar chart", "horizontal bar", "barchart")),
        ("column", ("column chart", "vertical bar", "columnchart")),
        ("pie", ("pie chart", "piechart")),
        ("donut", ("donut chart", "donutchart", "doughnut")),
        ("line", ("line chart", "linechart", "trend line")),
        ("area", ("area chart", "areachart")),
        ("radar", ("radar chart", "radarchart")),
        ("gauge", ("gauge chart", "gaugechart")),
        ("lollipop", ("lollipop chart", "lollipopchart")),
        ("heatmap", ("heatmap", "heat map")),
    ]
    for target_type, hints in chart_type_hints:
        if any(hint in blob for hint in hints):
            for chart in charts:
                if chart.get("type") in {"donut", "column", "bar"} and chart.get("type") != target_type:
                    chart["type"] = target_type
                    break


async def propose_dashboard(
    *,
    message: str,
    target: dict[str, Any],
    columns: list[str],
    sample_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fallback_kpis, fallback_charts = propose_generic(columns, message=message)
    try:
        payload = await chat_json(
            system=await dashboard_system_prompt("schema", _SYSTEM),
            user=_user_prompt(message, target, columns, sample_rows or []),
        )
        kpis = _normalize_kpis(payload.get("kpis"), columns)
        charts = _normalize_charts(payload.get("charts"), columns)
        if kpis or charts:
            apply_default_layout(kpis, charts)
            overlay_layout_from_message(message, kpis, charts)
            ensure_widget_descriptions(kpis, charts)
            repair_live_spec(kpis, charts, columns)
            rebind_sparse_group_columns(charts, sample_rows or [])
            kpis, charts = enrich_sparse_kpis(message, kpis, charts, columns)
            ensure_widget_descriptions(kpis, charts)
            return apply_ask_limits(message, kpis, charts)
    except LLMError as exc:
        logger.warning("dashboard_llm_propose_failed: %s", exc)
    except Exception as exc:
        logger.warning("dashboard_llm_propose_failed: %s", exc)
    overlay_layout_from_message(message, fallback_kpis, fallback_charts)
    repair_live_spec(fallback_kpis, fallback_charts, columns)
    rebind_sparse_group_columns(fallback_charts, sample_rows or [])
    return fallback_kpis, fallback_charts


def _filled_sample(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"none", "null", "nan"}


def _occupancy(columns: list[str], sample_rows: list[dict[str, Any]]) -> dict[str, float]:
    if not sample_rows:
        return {}
    out: dict[str, float] = {}
    for column in columns[:40]:
        filled = 0
        for row in sample_rows:
            if not isinstance(row, dict):
                continue
            value = row.get(column)
            if value is None:
                wanted = column.lower()
                for key, item in row.items():
                    if str(key).lower() == wanted:
                        value = item
                        break
            if _filled_sample(value):
                filled += 1
        out[column] = round(100.0 * filled / len(sample_rows), 1)
    return out


def _user_prompt(message: str, target: dict[str, Any], columns: list[str], sample_rows: list[dict[str, Any]]) -> str:
    rows: list[dict[str, Any]] = []
    budget = 24000
    used = 0
    for row in sample_rows:
        slim: dict[str, str] = {}
        for key, value in list(row.items())[:18]:
            slim[str(key)] = str(value)[:80]
        encoded = str(slim)
        if rows and used + len(encoded) > budget:
            break
        rows.append(slim)
        used += len(encoded)
    omitted = len(sample_rows) - len(rows)
    occupancy = _occupancy(columns, sample_rows)
    return (
        f"Design a dashboard strictly for this user request. Do not reuse a default widget list.\n"
        f"User request:\n{message or 'Build a dashboard for this repository.'}\n"
        f"CRITICAL REQUIREMENT: You MUST include widgets for EVERY feature, metric, grouping, and chart type specifically requested in the user prompt. Do not omit any requested feature.\n"
        f"If the user asks for specific chart types (e.g. bar, column, line, pie, donut, area, gauge, radar), use those exact types.\n"
        f"If the user asks for specific metrics or a specific number of KPIs, generate exactly what was asked.\n"
        f"Repository: {target.get('repository_name') or target.get('repository_id')}\n"
        f"Workflow: {target.get('workflow_name') or target.get('workflow_id') or ''}\n"
        f"Table: {target.get('qualified_table')}\n"
        f"Row count: {len(sample_rows)}\n"
        f"Columns: {columns}\n"
        f"Column occupancy (percent non-empty): {occupancy}\n"
        f"If InvoiceAmount occupancy is 0, still use it for money — not FileSize.\n"
        f"Items-table rows ({len(rows)} shown"
        + (f", {omitted} omitted for prompt size" if omitted else "")
        + f"): {rows}\n"
    )


def _normalize_kpis(items: Any, columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    seen: set[str] = set()
    bound = bind_columns(columns)
    for raw in (items or []):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("id") or "KPI").strip()
        widget_id = _slug(str(raw.get("id") or label), f"kpi_{len(out)+1}")
        if widget_id in seen:
            continue
        raw_agg = str(raw.get("agg") or "count").lower()
        agg = AGG_SYNONYMS.get(raw_agg, raw_agg)
        if agg not in ALLOWED_AGGS:
            agg = "count"
        column = resolve_column(columns, raw.get("column") or raw.get("value"))
        date_column = resolve_column(columns, raw.get("date_column") or raw.get("due") or raw.get("date"))
        match_column = resolve_column(columns, raw.get("match_column") or raw.get("match"))
        if agg in {"sum", "avg", "min", "max", "paid_sum", "outstanding_sum", "current_sum"} and not column:
            column = bound.get("amount")
            if not column:
                continue
        if agg.startswith("overdue"):
            if not column:
                column = bound.get("amount")
            if not date_column:
                date_column = bound.get("due")
            if not column and not date_column:
                continue
        seen.add(widget_id)
        cols: dict[str, str] = {}
        if column:
            cols["value"] = column
        if date_column:
            cols["date"] = date_column
        if match_column:
            cols["match"] = match_column
        item = {
            "id": widget_id,
            "label": label[:40],
            "description": _text_description(raw, _fallback_kpi_description(label, agg, cols)),
            "enabled": raw.get("enabled", True) is not False,
            "agg": agg,
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
    bound = bind_columns(columns)
    for raw in (items or []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("label") or raw.get("id") or "Chart").strip()
        widget_id = _slug(str(raw.get("id") or title), f"chart_{len(out)+1}")
        if widget_id in seen:
            continue
        raw_type = str(raw.get("type") or "donut").lower()
        chart_type = CHART_TYPE_SYNONYMS.get(raw_type, raw_type)
        if chart_type not in ALLOWED_TYPES:
            chart_type = "donut"
        group = resolve_column(columns, raw.get("group") or raw.get("category"))
        if not group or _norm(group) in SKIP_GROUP:
            group = bound.get("supplier") or bound.get("status") or bound.get("invoice_date") or bound.get("currency")
            if not group:
                continue
        value = resolve_column(columns, raw.get("value") or raw.get("column"))
        raw_agg = str(raw.get("agg") or ("sum" if value else "count")).lower()
        agg = AGG_SYNONYMS.get(raw_agg, raw_agg)
        if agg not in {"count", "sum", "avg", "min", "max", "outstanding_sum", "paid_sum"}:
            agg = "count"
        grain = str(raw.get("grain") or "none").lower()
        if grain not in {"month", "none", "aging", "payment"}:
            grain = "none"
        seen.add(widget_id)
        cols: dict[str, str] = {"group": group}
        if value:
            cols["value"] = value
        match_column = resolve_column(columns, raw.get("match_column") or raw.get("match"))
        paid_column = resolve_column(columns, raw.get("paid_column") or raw.get("paid"))
        if match_column:
            cols["match"] = match_column
        if paid_column:
            cols["paid"] = paid_column
        item = {
            "id": widget_id,
            "label": title[:60],
            "title": title[:60],
            "description": _text_description(
                raw, _fallback_chart_description(title, chart_type, cols, agg, grain)
            ),
            "type": chart_type,
            "enabled": raw.get("enabled", True) is not False,
            "agg": agg,
            "grain": grain,
            "columns": cols,
        }
        item.update(_layout_from(raw, len(out), kind="chart"))
        out.append(item)
    return out


def propose_generic(columns: list[str], *, message: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Column-driven widgets when the model is unavailable, guided by user message keywords."""
    by_norm = _col_map(columns)

    def pick(*hints: str) -> Optional[str]:
        for hint in hints:
            for key, name in by_norm.items():
                if hint in key:
                    return name
        return None

    numeric = pick(
        "invoiceamount",
        "invoicetotal",
        "amount",
        "total",
        "cost",
        "salary",
        "hours",
        "qty",
        "quantity",
        "score",
        "pages",
        "filesize",
        "size",
    )
    due = pick("duedate", "datedue", "paymentdue")
    date_col = pick("invoicedate", "createdatutc", "createdat", "modifiedatutc", "docdate", "date")
    match = pick("matchedstatus", "matchstatus", "matchstate", "paymentstatus")
    category = pick(
        "matchedstatus",
        "matchstatus",
        "department",
        "filetype",
        "currency",
        "category",
        "aistatus",
        "status",
        "type",
    )
    entity = pick("supplier", "vendor", "vendorname", "employee", "customer", "name")
    if entity and _norm(entity) in SKIP_GROUP:
        entity = None

    msg = (message or "").lower()
    kpis: list[dict[str, Any]] = []

    wants_count = any(kw in msg for kw in ("count", "number of", "records", "open", "invoices", "total files")) or not msg
    wants_total = any(kw in msg for kw in ("total", "sum", "amount", "spend", "value", "payable"))
    wants_avg = any(kw in msg for kw in ("avg", "average", "mean"))
    wants_overdue = any(kw in msg for kw in ("overdue", "past due", "late", "aging"))
    wants_paid = any(kw in msg for kw in ("paid", "settled", "cleared"))
    wants_outstanding = any(kw in msg for kw in ("outstanding", "unpaid", "pending", "open"))
    wants_entity = any(kw in msg for kw in ("supplier", "vendor", "customer", "employee"))

    if numeric and wants_total:
        kpis.append({"id": "total_value", "label": "TOTAL", "enabled": True, "agg": "sum", "columns": {"value": numeric}})
    if numeric and wants_avg:
        kpis.append({"id": "average_value", "label": "AVERAGE", "enabled": True, "agg": "avg", "columns": {"value": numeric}})
    if numeric and match and wants_outstanding:
        kpis.append(
            {
                "id": "outstanding",
                "label": "OUTSTANDING",
                "enabled": True,
                "agg": "outstanding_sum",
                "columns": {"value": numeric, "match": match},
            }
        )
    if numeric and match and wants_paid:
        kpis.append(
            {
                "id": "total_paid",
                "label": "TOTAL PAID",
                "enabled": True,
                "agg": "paid_sum",
                "columns": {"value": numeric, "match": match},
            }
        )
    if numeric and due and wants_overdue:
        kpis.append({"id": "overdue", "label": "OVERDUE", "enabled": True, "agg": "overdue_sum", "columns": {"value": numeric, "date": due, **({"match": match} if match else {})}})
    if entity and wants_entity:
        kpis.append({"id": "entity_count", "label": "SUPPLIERS", "enabled": True, "agg": "distinct", "columns": {"value": entity}})

    # If no specific KPI filtered or general ask, add standard metrics
    if not kpis or wants_count:
        kpis.insert(0, {"id": "record_count", "label": "RECORDS", "enabled": True, "agg": "count", "columns": {}})
    if numeric and not any(k["id"] == "total_value" for k in kpis):
        kpis.append({"id": "total_value", "label": "TOTAL", "enabled": True, "agg": "sum", "columns": {"value": numeric}})
    if numeric and not any(k["id"] == "average_value" for k in kpis):
        kpis.append({"id": "average_value", "label": "AVERAGE", "enabled": True, "agg": "avg", "columns": {"value": numeric}})
    if numeric and match and not any(k["id"] == "outstanding" for k in kpis):
        kpis.append(
            {
                "id": "outstanding",
                "label": "OUTSTANDING",
                "enabled": True,
                "agg": "outstanding_sum",
                "columns": {"value": numeric, "match": match},
            }
        )
    if numeric and due and not any(k["id"] == "overdue" for k in kpis):
        kpis.append({"id": "overdue", "label": "OVERDUE", "enabled": True, "agg": "overdue_sum", "columns": {"value": numeric, "date": due, **({"match": match} if match else {})}})
    if entity and not any(k["id"] == "entity_count" for k in kpis):
        kpis.append({"id": "entity_count", "label": "SUPPLIERS", "enabled": True, "agg": "distinct", "columns": {"value": entity}})

    charts: list[dict[str, Any]] = []
    pref_type = None
    for ct in ("bar", "column", "pie", "donut", "line", "area", "radar"):
        if ct in msg:
            pref_type = ct
            break

    if category:
        charts.append(_chart("by_category", "By status", pref_type or "donut", category, None, "count"))
    if entity and numeric:
        charts.append(_chart("by_entity", "By group", pref_type or "lollipop", entity, numeric, "sum"))
    elif entity:
        charts.append(_chart("by_entity", "By group", pref_type or "donut", entity, None, "count"))
    if date_col:
        charts.append(_chart("by_month", "By month", pref_type or "line", date_col, None, "count", grain="month"))
        if numeric:
            charts.append(_chart("value_by_month", "Value by month", pref_type or "area", date_col, numeric, "sum", grain="month"))
    if numeric and due:
        charts.append(_chart("aging", "Aging", pref_type or "column", due, numeric, "sum", grain="aging"))
    if category and numeric:
        charts.append(_chart("value_by_category", "Value by status", pref_type or "column", category, numeric, "sum"))
    kpis, charts = kpis, charts
    apply_default_layout(kpis, charts)
    overlay_layout_from_message(message, kpis, charts)
    ensure_widget_descriptions(kpis, charts)
    repair_live_spec(kpis, charts, columns)
    return apply_ask_limits(message, kpis, charts)


def _chart(
    widget_id: str,
    title: str,
    chart_type: str,
    group: str,
    value: Optional[str],
    agg: str,
    grain: str = "none",
    extra_cols: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    cols: dict[str, str] = {"group": group}
    if value:
        cols["value"] = value
    if extra_cols:
        cols.update({key: name for key, name in extra_cols.items() if name})
    return {
        "id": widget_id,
        "label": title,
        "title": title,
        "description": _fallback_chart_description(title, chart_type, cols, agg, grain),
        "type": chart_type,
        "enabled": True,
        "agg": agg,
        "grain": grain,
        "columns": cols,
    }
