"""Insight skill lock — force model output into locked insight_result JSON."""
from __future__ import annotations

from typing import Any, Optional

from app.insight_skills.rules import (
    DEFAULT_INSIGHTS_COUNT,
    apply_insight_highlights,
    resolve_insights_count,
)
from app.summary_skills.lock import loads_json_object, normalize_json_text


def insights_from(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("insight")
                    or item.get("message")
                    or item.get("value")
                )
                fact = str(text).strip() if text is not None else ""
            else:
                fact = str(item).strip()
            if fact:
                items.append(fact)
    elif isinstance(value, str) and value.strip():
        nested = loads_json_object(value)
        if isinstance(nested, list):
            return insights_from(nested)
        if isinstance(nested, dict) and "insights" in nested:
            return insights_from(nested.get("insights"))
        items = [value.strip()]
    return items


def locked_insight_payload(
    *,
    insights: Optional[list] = None,
    insights_count: int = DEFAULT_INSIGHTS_COUNT,
    insight_area: Optional[str] = None,
    source_text: str = "",
) -> dict:
    limit = resolve_insights_count(explicit=insights_count)
    items = insights_from(insights)[:limit]
    if items:
        items = apply_insight_highlights(items, source_text=source_text)
    payload: dict[str, Any] = {
        "insights": items,
        "insights_count": limit,
    }
    area = (insight_area or "").strip()
    if area:
        payload["insight_area"] = area
    return payload


def payload_from_parsed(
    data: dict,
    *,
    insights_count: int = DEFAULT_INSIGHTS_COUNT,
    insight_area: Optional[str] = None,
    source_text: str = "",
) -> dict:
    if not isinstance(data, dict):
        return locked_insight_payload(
            insights=[],
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )
    if "insights" not in data:
        for key in ("result", "data", "insight_result", "output"):
            nested = data.get(key)
            if isinstance(nested, dict) and "insights" in nested:
                data = nested
                break
            if isinstance(nested, list):
                return locked_insight_payload(
                    insights=nested,
                    insights_count=insights_count,
                    insight_area=insight_area,
                    source_text=source_text,
                )
    area = insight_area or data.get("insight_area")
    count = insights_count
    if data.get("insights_count") is not None:
        try:
            count = int(data["insights_count"])
        except (TypeError, ValueError):
            pass
    return locked_insight_payload(
        insights=data.get("insights"),
        insights_count=count,
        insight_area=str(area).strip() if area else None,
        source_text=source_text,
    )


def parse_insight_json_content(
    content: Any,
    *,
    insights_count: int = DEFAULT_INSIGHTS_COUNT,
    insight_area: Optional[str] = None,
    source_text: str = "",
) -> dict:
    if isinstance(content, dict):
        return payload_from_parsed(
            content,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )
    if isinstance(content, list):
        return locked_insight_payload(
            insights=content,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )

    text = normalize_json_text(content)
    if not text:
        return locked_insight_payload(
            insights=[],
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )

    data = loads_json_object(text)
    if isinstance(data, dict):
        return payload_from_parsed(
            data,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )

    import json as _json

    try:
        raw = _json.loads(text)
    except _json.JSONDecodeError:
        raw = None
    if isinstance(raw, list):
        return locked_insight_payload(
            insights=raw,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )
    if isinstance(raw, dict):
        return payload_from_parsed(
            raw,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )

    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return locked_insight_payload(
            insights=lines,
            insights_count=insights_count,
            insight_area=insight_area,
            source_text=source_text,
        )
    return locked_insight_payload(
        insights=[text] if text else [],
        insights_count=insights_count,
        insight_area=insight_area,
        source_text=source_text,
    )
