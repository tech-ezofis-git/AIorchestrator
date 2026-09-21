"""Insights for the data API HTML — follow the user message, like KPIs/charts."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.llm import LLMError, chat_json
from app.propose import wants_kpis_only

logger = logging.getLogger("v6_dashboard.insights")

_STOP = {
    "a", "an", "and", "all", "the", "for", "from", "with", "only", "just", "need",
    "i", "me", "my", "please", "show", "build", "make", "give", "want", "dashboard",
    "apply", "this", "that", "put", "on", "in", "of", "to", "at",
}

_SYSTEM = """You write dashboard insights that answer THIS user request using live KPI/chart numbers.

Return JSON only:
{"insights":["sentence","sentence","sentence"]}

The user request is the spec. Different requests MUST produce different insights.
Write only about widgets in the snapshot. The snapshot is already filtered to this request.
Do not emit a canned Accounts Payable pack (total invoices, payable, paid, outstanding, overdue) unless those widgets are in the snapshot and the user asked for a full AP overview.
Do not recap every KPI. Answer the request.

Rules:
- 3 to 5 short sentences. Lead with what they asked, not a generic overview.
- Use only numbers and labels in the snapshot. Do not invent aging buckets or percentages.
- Name vendor and status labels when those series exist.
- If a chart is outstanding amount by vendor, say outstanding amount — never "risk score" unless a risk metric is present.
- Do not mention payable, overdue, or vendors unless those widgets are in the snapshot.
- If fewer than 15 records, do not lead with month-over-month percentage changes.
- No markdown, no bullet characters inside the strings.
"""


def _fmt_value(item: dict[str, Any] | None) -> str:
    if not item or item.get("value") is None:
        return "—"
    value = item.get("value")
    try:
        number = float(value)
        text = f"{number:,.2f}".rstrip("0").rstrip(".") if not number.is_integer() else f"{int(number):,}"
    except (TypeError, ValueError):
        text = str(value)
    unit = item.get("unit")
    return f"{text} {unit}" if unit else text


def _fmt_raw(value: Any) -> str:
    return _fmt_value({"value": value})


def _tokens(text: str) -> set[str]:
    blob = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {part for part in blob.split() if part and part not in _STOP and (len(part) > 2 or part in {"ap", "hr", "po"})}


def user_ask(message: str, kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> str:
    """Prefer the real user message; if the data call sent 'apply', use widget copy."""
    text = re.sub(r"\s+", " ", (message or "").strip())
    if text and text.lower() not in {"apply", "i need a dashboard", "i need a dashboard."}:
        return text
    parts: list[str] = []
    for item in list(kpis or []) + list(charts or []):
        if item.get("enabled") is False:
            continue
        bit = str(item.get("description") or item.get("title") or item.get("label") or "").strip()
        if bit:
            parts.append(bit)
    return " ".join(parts[:8])


def _item_blob(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("id", "label", "title", "description", "agg", "type", "grain")
    ).lower()


def _score(item: dict[str, Any], ask_tokens: set[str]) -> int:
    blob = _item_blob(item)
    score = 0
    for token in ask_tokens:
        if token in blob:
            score += 3
    hints = (
        ("overdue", "past", "late", "aging"),
        ("vendor", "supplier", "risk", "radar"),
        ("match", "matched", "unmatched", "payment"),
        ("status",),
        ("department", "employee", "hr", "file"),
        ("aging", "bucket"),
    )
    for group in hints:
        if ask_tokens & set(group) and any(word in blob for word in group):
            score += 4
    return score


def _focus_widgets(
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    ask: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep widgets that match the ask so insights are not a full default recap."""
    tokens = _tokens(ask)
    enabled_kpis = [item for item in kpis if item.get("enabled") is not False]
    enabled_charts = [item for item in charts if item.get("enabled") is not False]
    if not tokens:
        return enabled_kpis, enabled_charts
    scored_kpis = [(item, _score(item, tokens)) for item in enabled_kpis]
    scored_charts = [(item, _score(item, tokens)) for item in enabled_charts]
    if not any(score > 0 for _, score in scored_kpis + scored_charts):
        return enabled_kpis, enabled_charts
    focused_kpis = [item for item, score in scored_kpis if score > 0]
    focused_charts = [item for item, score in scored_charts if score > 0]
    if not focused_kpis and not focused_charts:
        return enabled_kpis, enabled_charts
    return focused_kpis, focused_charts


def _snapshot(
    *,
    repository_name: str,
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    data: dict[str, Any],
    ask: str,
) -> dict[str, Any]:
    kpi_data = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
    chart_data = data.get("charts") if isinstance(data.get("charts"), dict) else {}
    kpi_rows = []
    for item in kpis:
        if item.get("enabled") is False:
            continue
        widget_id = str(item.get("id") or "")
        row = kpi_data.get(widget_id) if isinstance(kpi_data.get(widget_id), dict) else {}
        kpi_rows.append(
            {
                "id": widget_id,
                "label": item.get("label") or widget_id,
                "description": item.get("description") or "",
                "value": row.get("value"),
                "trend_pct": row.get("trend_pct"),
            }
        )
    chart_rows = []
    for item in charts:
        if item.get("enabled") is False:
            continue
        widget_id = str(item.get("id") or "")
        row = chart_data.get(widget_id) if isinstance(chart_data.get(widget_id), dict) else {}
        series = [
            {"name": part.get("name"), "value": part.get("value")}
            for part in (row.get("series") or [])[:6]
            if isinstance(part, dict)
        ]
        cats = list(row.get("categories") or [])[:6]
        vals = list(row.get("values") or [])[:6]
        chart_rows.append(
            {
                "id": widget_id,
                "title": item.get("title") or item.get("label") or widget_id,
                "description": item.get("description") or "",
                "type": item.get("type") or row.get("type"),
                "series": series or None,
                "categories": cats or None,
                "values": vals or None,
            }
        )
    return {"repository": repository_name, "user_request": ask, "kpis": kpi_rows, "charts": chart_rows}


def _chart_sentence(item: dict[str, Any], row: dict[str, Any]) -> str | None:
    title = str(item.get("title") or item.get("label") or item.get("id") or "Chart")
    series = [part for part in (row.get("series") or []) if isinstance(part, dict)]
    if series:
        ranked = sorted(series, key=lambda part: float(part.get("value") or 0), reverse=True)
        top = ranked[0]
        name = str(top.get("name") or "Unknown")
        return f"{title}: {name} is the largest slice at {_fmt_raw(top.get('value'))}."
    cats = list(row.get("categories") or [])
    vals = list(row.get("values") or [])
    if cats and vals:
        try:
            index = max(range(len(vals)), key=lambda i: float(vals[i] or 0))
        except (TypeError, ValueError):
            index = 0
        return f"{title}: {cats[index]} leads at {_fmt_raw(vals[index])}."
    return None


def fallback_insights(
    *,
    repository_name: str,
    kpis: list[dict[str, Any]],
    data: dict[str, Any],
    charts: list[dict[str, Any]] | None = None,
    message: str = "",
) -> list[str]:
    charts = charts or []
    if wants_kpis_only(message) or wants_kpis_only(user_ask(message, kpis, charts)):
        return []
    ask = user_ask(message, kpis, charts)
    kpis, charts = _focus_widgets(kpis, charts, ask)
    kpi_data = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
    chart_data = data.get("charts") if isinstance(data.get("charts"), dict) else {}
    ask_tokens = _tokens(ask)
    ranked_kpis = sorted(kpis, key=lambda item: _score(item, ask_tokens), reverse=True)
    ranked_charts = sorted(charts, key=lambda item: _score(item, ask_tokens), reverse=True)
    lines: list[str] = []
    seen: set[str] = set()

    def add(text: str | None, key: str) -> None:
        if not text or key in seen or len(lines) >= 5:
            return
        seen.add(key)
        lines.append(text[:240])

    for item in ranked_charts:
        widget_id = str(item.get("id") or "")
        row = chart_data.get(widget_id) if isinstance(chart_data.get(widget_id), dict) else {}
        add(_chart_sentence(item, row), f"chart:{widget_id}")

    for item in ranked_kpis:
        widget_id = str(item.get("id") or "")
        row = kpi_data.get(widget_id) if isinstance(kpi_data.get(widget_id), dict) else {}
        label = str(item.get("label") or widget_id)
        desc = str(item.get("description") or "").strip().rstrip(".")
        if desc and desc.lower() != label.lower():
            add(f"{label}: {desc} is currently {_fmt_value(row)}.", f"kpi:{widget_id}")
        else:
            add(f"{label} is currently {_fmt_value(row)}.", f"kpi:{widget_id}")
        if len(lines) >= 4:
            break

    if not lines:
        name = repository_name or "this repository"
        add(f"{name} has no enabled widgets to summarize for this request.", "empty")
    return lines[:5]


async def generate_insights(
    *,
    repository_name: str,
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    data: dict[str, Any],
    message: str = "",
) -> list[str]:
    ask = user_ask(message, kpis, charts)
    if wants_kpis_only(message) or wants_kpis_only(ask):
        return []
    focus_kpis, focus_charts = _focus_widgets(kpis, charts, ask)
    snapshot = _snapshot(
        repository_name=repository_name,
        kpis=focus_kpis,
        charts=focus_charts,
        data=data,
        ask=ask,
    )
    try:
        payload = await chat_json(
            system=_SYSTEM,
            user=(
                f"Design insights for this user request. Do not reuse a default insight list.\n"
                f"User request:\n{ask or 'Summarize the enabled widgets.'}\n"
                f"Snapshot: {snapshot}"
            ),
            timeout=30.0,
            temperature=0.5,
        )
        items = payload.get("insights")
        lines: list[str] = []
        if isinstance(items, list):
            for item in items:
                text = str(item).strip() if item is not None else ""
                if text:
                    lines.append(text[:240])
        if lines:
            return lines[:5]
    except LLMError as exc:
        logger.warning("dashboard_insights_failed: %s", exc)
    except Exception as exc:
        logger.warning("dashboard_insights_failed: %s", exc)
    return fallback_insights(
        repository_name=repository_name,
        kpis=kpis,
        charts=charts,
        data=data,
        message=message,
    )
