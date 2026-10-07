"""Prompt understanding and requirement extraction for Dashboard Agent.

Extracts domain, purpose, KPIs, charts, tables, filters, interactions,
and layout requirements dynamically from the user's prompt.
No hardcoded domain logic or predefined dashboard schemas.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.dashboard.llm import LLMError, chat_json

logger = logging.getLogger("orchestrator.dashboard.analyzer")

_ANALYZER_SYSTEM = """You are an expert dashboard requirements analyst.
Analyze the user's dashboard prompt dynamically. Extract all explicit and implicit requirements.
The user prompt is the SOLE source of truth. Do NOT assume a domain or business process unless derived from the prompt or provided repository context.
Do NOT invent unrequested business metrics, charts, or filters.

Return JSON only:
{
  "title": "Short descriptive title for the dashboard",
  "domain": "Business domain (e.g. Vessel Operations, RFQ Lifecycle, HR Analytics, Sales, Finance, Inventory, etc.)",
  "purpose": "Core purpose of the requested dashboard",
  "kpis": [
    {
      "name": "KPI Name",
      "metric": "What is measured",
      "aggregation": "count|sum|avg|min|max|distinct|ratio",
      "requirement": "Exact phrase or concept from prompt",
      "formatting": "number|currency|percent"
    }
  ],
  "charts": [
    {
      "title": "Chart Title",
      "type": "bar|line|area|pie|donut|column|funnel|gauge|radar|heatmap|lollipop",
      "dimension": "Dimension/grouping concept requested",
      "measure": "Measure concept requested",
      "aggregation": "count|sum|avg|min|max",
      "grain": "none|month|day|year",
      "requirement": "Exact phrase from prompt"
    }
  ],
  "tables": [
    {
      "title": "Table Title",
      "columns": ["Column1", "Column2", "..."],
      "requirement": "Exact phrase from prompt"
    }
  ],
  "filters": [
    {
      "label": "Filter Label",
      "field_concept": "field name or concept",
      "type": "select|date|date_range|search|numeric_range",
      "requirement": "Exact phrase from prompt"
    }
  ],
  "interactions": [
    {
      "type": "drill_down|inspect_drawer|filter_sync",
      "description": "Interaction requested",
      "requirement": "Exact phrase from prompt"
    }
  ],
  "layout": {
    "theme": "light|dark|auto",
    "structure": "responsive_grid|sidebar|two_column",
    "kpi_position": "top|bottom"
  },
  "requirements_summary": [
    "Summary of requirement 1",
    "Summary of requirement 2"
  ]
}
"""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _title_case(text: str) -> str:
    parts = []
    for word in _clean_text(text).split():
        parts.append(word if word.isupper() and 2 <= len(word) <= 5 else word.capitalize())
    return " ".join(parts)


def _has_trigger(text: str, trigger: str) -> bool:
    """Match chart words as whole words so 'pipeline' is not read as a line chart."""
    if " " in trigger or "-" in trigger:
        return trigger in text
    return re.search(rf"\b{re.escape(trigger)}\b", text) is not None


_COUNT_NOUNS = {
    "call", "calls", "record", "records", "item", "items", "rfq", "rfqs",
    "invoice", "invoices", "order", "orders", "sku", "skus", "ticket", "tickets",
    "employee", "employees", "vessel", "vessels", "customer", "customers",
    "lead", "leads", "quote", "quotes",
}
_SUM_WORDS = {
    "amount", "spend", "revenue", "cost", "valuation", "value", "price", "fuel",
    "hours", "minutes", "quantity", "qty", "score", "delay",
}


def infer_aggregation(label: str, explicit: str | None = None) -> str:
    """Choose an aggregation from the requested metric, not from a generic total template."""
    text = (label or "").lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    mapped = ""
    if explicit:
        mapped = str(explicit).strip().lower()
        if mapped in {"average", "mean"}:
            mapped = "avg"
        elif mapped in {"unique", "unique_count", "distinct_count"}:
            mapped = "distinct"
        elif mapped in {"minimum", "lowest"}:
            mapped = "min"
        elif mapped in {"maximum", "highest"}:
            mapped = "max"
        elif mapped == "total":
            mapped = "sum"
    if mapped in {"avg", "min", "max", "distinct", "ratio"}:
        return mapped
    if words & {"average", "avg", "mean"}:
        return "avg"
    if words & {"min", "minimum", "lowest"}:
        return "min"
    if words & {"max", "maximum", "highest", "longest"}:
        return "max"
    if words & {"distinct", "unique"} or "sku count" in text:
        return "distinct"
    countish = bool(words & _COUNT_NOUNS) or "number of" in text
    sumish = bool(words & _SUM_WORDS)
    if countish and not sumish:
        return "count"
    if sumish or mapped == "sum":
        return "sum"
    if mapped in {"count", "sum", "avg", "min", "max", "distinct", "ratio"}:
        return mapped
    return "count"


def analyze_prompt_heuristic(
    message: str,
    target: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """Dynamic heuristic extractor when LLM is unavailable."""
    raw_msg = (message or "").strip()
    msg = _clean_text(message)
    msg_lower = msg.lower()

    # Determine domain dynamically
    repo_name = str(target.get("repository_name") or target.get("workflow_name") or "").strip()
    domain_guess = ""
    if repo_name and repo_name.lower() not in {"this repository", "items", "repository", "dashboard", "custom", "none"}:
        domain_guess = repo_name
    else:
        dom_match = re.search(
            r"(?:dashboard\s+for|dashboard\s+on|create\s+(?:a|an)?|build\s+(?:a|an)?|show\s+(?:a|an)?|generate\s+(?:a|an)?)\s+([a-z0-9\s&/-]+?)(?:\s+dashboard|\s+with|\s+to|\s+for|\.|\:|\n|$)",
            msg,
            re.I,
        )
        if dom_match and len(dom_match.group(1).strip()) > 2:
            extracted_dom = _title_case(dom_match.group(1).strip())
            for gw in ["Modern", "Dynamic", "Enterprise", "Executive", "Operational", "Comprehensive"]:
                if extracted_dom.startswith(gw + " "):
                    extracted_dom = extracted_dom[len(gw) + 1:].strip()
            domain_guess = extracted_dom

        if not domain_guess:
            if "vessel" in msg_lower or "berth" in msg_lower or "port" in msg_lower:
                domain_guess = "Vessel Operations"
            elif "rfq" in msg_lower or "quote" in msg_lower or "freight" in msg_lower or "ftl" in msg_lower:
                domain_guess = "RFQ Management"
            elif "invoice" in msg_lower or "payable" in msg_lower or "spend" in msg_lower or "ap" in msg_lower:
                domain_guess = "Invoice & AP Management"
            elif "hr" in msg_lower or "employee" in msg_lower or "onboarding" in msg_lower:
                domain_guess = "HR Operations"
            elif "inventory" in msg_lower or "stock" in msg_lower or "warehouse" in msg_lower:
                domain_guess = "Inventory Management"
            elif "sales" in msg_lower or "lead" in msg_lower or "pipeline" in msg_lower:
                domain_guess = "Sales Pipeline"
            elif "ticket" in msg_lower or "incident" in msg_lower or "support" in msg_lower:
                domain_guess = "Support & Incident Tracking"
            elif "patient" in msg_lower or "hospital" in msg_lower or "medical" in msg_lower:
                domain_guess = "Healthcare Operations"
            else:
                domain_guess = "Operations"

    # Derive title
    title_match = re.search(
        r"^(?:create|build|show|generate)\s+(?:an|a)\s+(.*?)(?:\s+dashboard\b|\.|$)",
        msg,
        re.I,
    )
    if title_match and title_match.group(1).strip():
        raw_title = _title_case(title_match.group(1))
        title = f"{raw_title} Dashboard" if "dashboard" not in raw_title.lower() else raw_title
    elif repo_name and repo_name.lower() not in {"this repository", "items", "repository", "dashboard", "custom", "none"}:
        title = f"{repo_name} Dashboard"
    else:
        title = f"{domain_guess} Dashboard" if "dashboard" not in domain_guess.lower() else domain_guess

    # Extract requested KPIs. Prefer an explicit card list over the first sentence.
    kpis = _kpis_from_card_list(raw_msg)
    if not kpis:
        kpis = _kpis_from_sentence(raw_msg)

    sections = _sections_from_prompt(raw_msg)
    charts: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    insight_topics: list[str] = []
    if sections:
        charts, tables, insight_topics = _components_from_sections(sections, columns)
    else:
        charts = _charts_from_clauses(raw_msg)
        if any(w in msg_lower for w in ["table", "list", "grid", "register", "records"]):
            tables.append({
                "title": f"Detailed {domain_guess} Table",
                "columns": columns[:12] if columns else [],
                "requirement": "Table requested in prompt",
            })

    filters = _filters_from_prompt(raw_msg)

    # Legacy sentence scan kept only when the prompt did not name filters.
    if not filters:
        filters = _filters_from_sentence(raw_msg)

    interactions: list[dict[str, Any]] = []
    if "click" in msg_lower or "detail" in msg_lower or "drill" in msg_lower or "searchable" in msg_lower:
        interactions.append({
            "type": "inspect_drawer",
            "description": f"Click a row to inspect the {domain_guess.lower()} record",
            "requirement": "Interaction requested in prompt",
        })

    layout = {
        "theme": "dark" if "dark" in msg_lower else "light",
        "structure": "two_column" if "two-column" in msg_lower or "two column" in msg_lower else "responsive_grid",
        "kpi_position": "top",
    }

    reqs_summary = []
    if kpis:
        reqs_summary.append(f"{len(kpis)} KPIs requested: " + ", ".join(k["name"] for k in kpis))
    if charts:
        reqs_summary.append(f"{len(charts)} Charts requested: " + ", ".join(c["title"] for c in charts))
    if filters:
        reqs_summary.append("Filters: " + ", ".join(f["label"] for f in filters))
    if tables:
        reqs_summary.append(f"{len(tables)} tables requested")
    if insight_topics:
        reqs_summary.append("Insights: " + ", ".join(insight_topics[:8]))
    if interactions:
        reqs_summary.append("Interactive drill-down requested")

    return {
        "title": title,
        "domain": domain_guess,
        "purpose": f"Operational visibility into {domain_guess.lower()}",
        "kpis": kpis,
        "charts": charts,
        "tables": tables,
        "filters": filters,
        "insights": insight_topics,
        "interactions": interactions,
        "layout": layout,
        "requirements": reqs_summary,
        "requirements_summary": reqs_summary,
    }


def _kpis_from_card_list(msg: str) -> list[dict[str, Any]]:
    match = re.search(
        r"(?:(?:\b|^)(?:(?:show|include|with|have|display|create)\s+.*?)?(?:(\d+)\s+)?(?:kpis?|key performance indicators?)(?:\s*cards?|\s*metrics?)?(?:\s*\([^)]*\))?(?:\s*(?:at the top|at the bottom|on top|on bottom|to show|to include|including|include|for the dashboard|for))?\s*[:\n])\s*(.+?)(?=(?:\n\s*\n\s*[A-Z][a-zA-Z\s]+:|[.\n]\s*(?:include these|dashboard sections?|sections? to include|sections?\s*:|charts?\s*:|tables?\s*:|filters?\s*:|insights?\s*:|interactions?\s*:)|(?:\b|\s)(?:include|also include|add|show)\s+(?:these\s+)?(?:\d+\s+)?(?:dashboard\s+)?(?:charts?|tables?|filters?|sections?)\b|\Z))",
        msg,
        re.I | re.S,
    )
    if not match:
        return []
    items = _split_list(match.group(2))
    return _kpi_items(items)


def _kpis_from_sentence(msg: str) -> list[dict[str, Any]]:
    kpis: list[dict[str, Any]] = []
    show_kpi_match = re.search(
        r"(?:show|include|with|have)?\s*(?:\d+\s+)?(?:kpis?|kpi cards?|metrics?)(?:\s*\([^)]*\))?\s*(?:to show|to include|including|include)?\s*[:\s]+([^.\n]+?)(?:add|allow|filter|click|monthly|weekly|detailed|\.|$)",
        msg,
        re.I,
    )
    if not show_kpi_match:
        show_kpi_match = re.search(
            r"(?:show|include|with)\s+([^.\n]+?)(?:add|allow|filter|click|monthly|weekly|detailed|\.|$)",
            msg,
            re.I,
        )
    if show_kpi_match:
        kpi_phrase = show_kpi_match.group(1)
        items = _split_list(kpi_phrase)
        return _kpi_items(items)
    return kpis


def _kpi_items(parts: list[str]) -> list[dict[str, Any]]:
    kpis: list[dict[str, Any]] = []
    for part in parts:
        item_clean = re.sub(r"^\s*(?:\d+[.)]|\*|-|•|\band\b)\s*", "", part, flags=re.I).strip()
        item_clean = _clean_text(item_clean).strip(" -*.,:")
        if not item_clean or len(item_clean) < 3:
            continue
        if any(skip in item_clean.lower() for skip in ["chart", "these dashboard", "sections", "include these", "dashboard sections", "cards at the top", "cards on top"]):
            continue
        name = _title_case(item_clean)
        if any(k["name"] == name for k in kpis):
            continue
        kpis.append({
            "name": name,
            "metric": item_clean,
            "aggregation": infer_aggregation(item_clean),
            "formatting": "percent" if any(w in item_clean.lower() for w in ["rate", "percentage", "ratio", "%"]) else "number",
            "requirement": f"Requested KPI '{item_clean}'",
        })
    return kpis


def _split_list(text: str) -> list[str]:
    parts = re.split(
        r"\r?\n\s*(?:\d+[.)]|\*|-|•)?\s*|(?:\b|^)\d+[.)]\s+|\s*[-*•]\s+|,;\s*|,(?!\s*\d)\s*(?:and\s+)?|\s+;\s*|\s+\band\b\s+",
        text or "",
        flags=re.I,
    )
    results = []
    for part in parts:
        cleaned = re.sub(r"^\s*(?:\d+[.)]|\*|-|•|\band\b)\s*", "", part, flags=re.I).strip()
        cleaned = _clean_text(cleaned).strip(" .:,")
        if cleaned:
            results.append(cleaned)
    return results


def _sections_from_prompt(msg: str) -> list[dict[str, Any]]:
    sec_block_match = re.search(
        r"(?:dashboard\s+sections?|sections?\s+to\s+include|include\s+these(?:\s+dashboard)?\s+sections?|(?:also\s+)?(?:include|show|add)\s+(?:\d+\s+)?(?:dashboard\s+)?(?:charts?|sections?|visualizations?)|charts?\s*to\s*include|include\s*these\s*(?:\d+\s+)?charts?|charts?\s*:|visualizations?\s*:)\s*[:\n]\s*(.+?)(?=(?:\n\s*\n\s*[A-Z][a-zA-Z\s]+:|\n\s*(?:filters?|insights?|tables?|actions?)\b|\b(?:add|include)\s+filters?\b|\Z))",
        msg,
        re.I | re.S,
    )
    is_charts_block = False
    if sec_block_match:
        search_text = sec_block_match.group(1)
        header_text = sec_block_match.group(0)[:60].lower()
        if "chart" in header_text or "visualization" in header_text:
            is_charts_block = True
    else:
        search_text = re.sub(
            r"(?:(?:show|include|with|have)?\s*(?:\d+\s+)?kpis?(?:\s*cards?|\s*metrics?)?(?:\s*\([^)]*\))?(?:\s*(?:to show|to include|including|include))?\s*[:\n]|key performance indicators?\s*[:\n])\s*.+?(?=(?:\n\s*\n|\n\s*(?:include these|dashboard sections?|sections?|charts?|tables?|filters?|insights?)\b|\Z))",
            "",
            msg,
            flags=re.I | re.S,
        )

    sections: list[dict[str, Any]] = []
    for match in re.finditer(
        r"(?:^|\n|\s)(\d+)[.)]\s+(.+?)(?=(?:(?:\n|\s)\d+[.)]\s+)|\Z)",
        search_text,
        re.S,
    ):
        body = _clean_text(match.group(2))
        if len(body) < 5:
            continue
        title_match = re.match(r"(.+?)(?:\s+(?:Show|Display|Create|Add)\b|:)", body, re.I)
        title = _title_case((title_match.group(1) if title_match else body)[:80])
        if not title:
            continue
        sections.append({
            "number": match.group(1),
            "title": title,
            "body": body,
            "is_chart_block": is_charts_block,
        })

    # If no numbered sections, try bullet points or lines
    if not sections and sec_block_match:
        for match in re.finditer(
            r"(?:^|\n)\s*[-*•]\s+(.+?)(?=(?:\n\s*[-*•]\s+)|\Z)",
            search_text,
            re.S,
        ):
            body = _clean_text(match.group(1))
            if len(body) < 5:
                continue
            title_match = re.match(r"(.+?)(?:\s+(?:Show|Display|Create|Add)\b|:)", body, re.I)
            title = _title_case((title_match.group(1) if title_match else body)[:80])
            if not title:
                continue
            sections.append({
                "number": str(len(sections) + 1),
                "title": title,
                "body": body,
                "is_chart_block": is_charts_block,
            })

    return sections


def _components_from_sections(
    sections: list[dict[str, Any]],
    columns: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    charts: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    insights: list[str] = []
    for section in sections:
        title = section["title"]
        body = section["body"]
        blob = f"{title} {body}".lower()

        # Extract insights if present
        if "insight" in blob or "bullet list" in blob or "key takeaways" in blob:
            for topic in re.split(r"\s+-\s+", body):
                topic_clean = _clean_text(re.sub(r"^.*insights?\s*", "", topic, flags=re.I))
                if len(topic_clean) > 8 and "insight" not in topic_clean.lower()[:12]:
                    insights.append(topic_clean[:160])
            if not insights:
                insights.append(title)

        # Extract tables if present
        if re.search(r"\b(table|searchable|sortable|grid|data\s+table|records?\s+table|latest\s+calls|recent\s+calls|recent\s+vessel|latest\s+vessel|recent\s+records)\b", blob) or "with:" in blob or "containing:" in blob:
            requested = _field_list(body)
            tables.append({
                "title": title[:80],
                "columns": requested or columns[:12],
                "requirement": body[:240],
            })

        # Visual chart classification
        chart_type = "bar"
        grain = "none"
        if "pipeline" in blob or "→" in section["body"] or "->" in section["body"] or "funnel" in blob or "conversion" in blob:
            chart_type = "funnel"
        elif "gauge" in blob or "meter" in blob or "utilization" in blob:
            chart_type = "gauge"
        elif "radar" in blob or "spider" in blob or "bottleneck" in blob:
            chart_type = "radar"
        elif "heatmap" in blob or "heat map" in blob or "matrix" in blob:
            chart_type = "heatmap"
        elif "lollipop" in blob:
            chart_type = "lollipop"
        elif "line" in blob or "trend" in blob or "timeline" in blob or "over time" in blob:
            chart_type = "line"
            if "daily" in blob or "by day" in blob:
                grain = "day"
            elif "weekly" in blob or "by week" in blob:
                grain = "week"
            else:
                grain = "month"
        elif "area" in blob:
            chart_type = "area"
        elif "column" in blob or "vertical bar" in blob or "performance" in blob:
            chart_type = "column"
        elif "donut" in blob or "doughnut" in blob or "status" in blob or "document" in blob or any(word in blob for word in ("overview", "breakdown", "distribution", "mix")):
            chart_type = "donut"
        elif "pie" in blob:
            chart_type = "pie"
        elif "bar" in blob or "horizontal bar" in blob or "ranking" in blob or "delay" in blob or "schedule" in blob:
            chart_type = "bar"

        # Dimension extraction
        dimension = ""
        by_match = re.search(r"(?:by|per|across|ranking)\s+([a-z0-9 _-]{2,40})", blob)
        wise_match = re.search(r"([a-z0-9]+)-wise", blob)
        if by_match:
            dimension = _clean_text(by_match.group(1))
        elif wise_match:
            dimension = wise_match.group(1)
        elif "status" in blob:
            dimension = "status"
        elif "port" in blob:
            dimension = "port"
        elif "carrier" in blob:
            dimension = "carrier"
        elif "vessel" in blob:
            dimension = "vessel"
        elif "reason" in blob or "cause" in blob or "delay" in blob:
            dimension = "reason"
        else:
            dim_cand = re.sub(r"\b(breakdown|distribution|trend|overview|analysis|chart|ranking|pipeline|register|table|details|insights)\b", "", title, flags=re.I).strip()
            dimension = _clean_text(dim_cand) if dim_cand else "category"

        # Measure & Aggregation extraction
        agg = infer_aggregation(blob)
        measure = ""
        measure_keywords = [
            "amount", "spend", "revenue", "cost", "valuation", "value", "price", "fuel",
            "tonnage", "hours", "minutes", "quantity", "qty", "score", "delay", "duration",
            "speed", "stay", "turnaround", "rate", "salary", "balance", "count",
        ]
        for mk in measure_keywords:
            if re.search(rf"\b{re.escape(mk)}\b", blob):
                measure = mk
                if agg == "count" and mk not in {"count", "rate"}:
                    agg = "sum"
                break

        charts.append({
            "title": title[:80],
            "type": chart_type,
            "dimension": dimension,
            "measure": measure or None,
            "aggregation": agg,
            "grain": grain,
            "requirement": body[:240],
        })
    return charts, tables, insights


def _field_list(body: str) -> list[str]:
    match = re.search(r"(?:with|containing):\s*(.+)$", body, re.I)
    source = match.group(1) if match else ""
    if not source:
        return []
    fields = []
    for part in re.split(r",|\band\b", source):
        field = _clean_text(part).strip(" .")
        if field and len(field) > 1:
            fields.append(field)
    return fields


def _filters_from_prompt(msg: str) -> list[dict[str, Any]]:
    match = re.search(r"filters?\s+for:\s*(.+?)(?:\.\s|\n\s*\n|\Z)", msg, re.I | re.S)
    if not match:
        return []
    filters = []
    for part in _split_list(match.group(1)):
        if not part or len(part) < 2:
            continue
        label = _title_case(part.strip(" ."))
        kind = "date" if re.search(r"date|timeframe|period", part, re.I) else "select"
        filters.append({
            "label": label,
            "field_concept": part.lower().replace(" ", "_").replace("/", "_"),
            "type": kind,
            "requirement": f"Filter by '{part}'",
        })
    return filters


def _filters_from_sentence(msg: str) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    filter_match = re.search(r"(?:filter|filtering)\s+(?:by|on)\s+([^.\n]+)", msg, re.I)
    if not filter_match:
        return filters
    for part in re.split(r",|\band\b", filter_match.group(1)):
        f_clean = _clean_text(part)
        if not f_clean:
            continue
        ftype = "date" if "date" in f_clean.lower() or "time" in f_clean.lower() else "select"
        filters.append({
            "label": _title_case(f_clean),
            "field_concept": f_clean.lower().replace(" ", "_"),
            "type": ftype,
            "requirement": f"Filter by '{f_clean}'",
        })
    return filters


def _charts_from_clauses(msg: str) -> list[dict[str, Any]]:
    clean_msg = re.sub(
        r"(?:(?:show|include|with|have)?\s*(?:\d+\s+)?kpis?(?:\s*cards?|\s*metrics?)?(?:\s*(?:to show|to include|including|include))?\s*[:\n]|key performance indicators?\s*[:\n])\s*.+?(?=(?:\n\s*\n|\n\s*(?:include these|dashboard sections?|sections?|charts?|tables?|filters?|insights?)\b|\Z))",
        "",
        msg,
        flags=re.I | re.S,
    )
    charts: list[dict[str, Any]] = []
    chart_keywords = [
        ("line", ["line chart", "line", "trend", "timeline"]),
        ("bar", ["bar chart", "bar", "horizontal bar", "performance", "port-wise", "ranking"]),
        ("column", ["column chart", "column", "vertical bar"]),
        ("area", ["area chart", "area"]),
        ("donut", ["donut chart", "donut", "breakdown", "distribution", "mix", "split", "overview"]),
        ("pie", ["pie chart", "pie"]),
        ("funnel", ["funnel", "pipeline", "conversion"]),
        ("gauge", ["gauge", "meter", "utilization"]),
        ("radar", ["radar", "spider"]),
        ("heatmap", ["heatmap", "heat map", "matrix"]),
        ("lollipop", ["lollipop"]),
    ]

    # Split into clauses by sentences, commas, and conjunctions
    clauses = re.split(r"[,.\n;]|\band\b", clean_msg)
    for clause in clauses:
        c_clean = _clean_text(clause)
        c_lower = c_clean.lower()
        if not c_clean or len(c_clean) < 4:
            continue
        if any(skip in c_lower for skip in ["table", "filter", "kpi", "show total", "clicking", "detail"]):
            continue

        matched_type = None
        for chart_type, triggers in chart_keywords:
            if any(_has_trigger(c_lower, trigger) for trigger in triggers):
                matched_type = chart_type
                break
        if not matched_type and re.search(r"\bby\b|-wise", c_lower):
            matched_type = "bar"
        if not matched_type:
            continue
        title_cand = c_clean
        for prefix in [
            "create an ", "create a ", "build an ", "build a ",
            "add an ", "add a ", "add ", "include an ", "include a ", "include ",
            "show an ", "show a ", "show ", "an ", "a ",
        ]:
            if title_cand.lower().startswith(prefix):
                title_cand = title_cand[len(prefix):]
        title_cand = _title_case(title_cand)
        if any(c["title"] == title_cand for c in charts):
            continue
        grain = "month" if "month" in c_lower else ("day" if "daily" in c_lower or " by day" in c_lower else "none")
        if "trend" in c_lower or "over time" in c_lower or "timeline" in c_lower:
            matched_type = "line"
            if grain == "none":
                grain = "month"
        dimension = "category"
        by_match = re.search(r"(?:by|per)\s+([a-z0-9 _-]+)", c_lower)
        wise_match = re.search(r"([a-z0-9]+)-wise", c_lower)
        if by_match:
            dimension = _clean_text(by_match.group(1))
        elif wise_match:
            dimension = wise_match.group(1)
        elif "status" in c_lower:
            dimension = "status"
        charts.append({
            "title": title_cand,
            "type": matched_type,
            "dimension": dimension,
            "measure": "count",
            "aggregation": "count",
            "grain": grain,
            "requirement": f"Requested visualization '{c_clean}'",
        })
    return charts


async def analyze_prompt(
    *,
    message: str,
    target: dict[str, Any],
    columns: list[str],
    sample_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyzes user prompt with LLM and falls back to dynamic heuristic analyzer."""
    heuristic = analyze_prompt_heuristic(message, target, columns)
    if not message.strip():
        return heuristic

    user_payload = (
        f"User Prompt:\n{message}\n\n"
        f"Available Context:\n"
        f"- Repository/Workflow: {target.get('repository_name') or target.get('workflow_name') or 'Custom'}\n"
        f"- Table: {target.get('qualified_table')}\n"
        f"- Available Columns: {columns[:30]}\n"
    )

    try:
        data = await chat_json(
            system=_ANALYZER_SYSTEM,
            user=user_payload,
            temperature=0.1,
        )
        if isinstance(data, dict) and (data.get("kpis") or data.get("charts") or data.get("title")):
            data.setdefault("title", heuristic["title"])
            data.setdefault("domain", heuristic["domain"])
            data.setdefault("purpose", heuristic["purpose"])
            for key in ("kpis", "charts", "tables", "filters", "insights"):
                model_items = data.get(key) if isinstance(data.get(key), list) else []
                heuristic_items = heuristic.get(key) if isinstance(heuristic.get(key), list) else []
                if len(model_items) < len(heuristic_items):
                    data[key] = heuristic_items
            data.setdefault("interactions", heuristic["interactions"])
            data.setdefault("layout", heuristic["layout"])
            data.setdefault("requirements", heuristic["requirements"])
            data.setdefault("requirements_summary", heuristic["requirements_summary"])
            return data
    except (LLMError, Exception) as exc:
        logger.info("analyze_prompt_llm_skipped_or_failed (%s), using dynamic heuristic", exc)

    return heuristic
