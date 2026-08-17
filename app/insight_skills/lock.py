"""Insight skill lock — force model output into {insights: [str, ...]}."""
from __future__ import annotations

from typing import Any, Optional

from app.summary_skills.lock import loads_json_object, normalize_json_text


def insights_from(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                # Allow {"text": "..."} / {"insight": "..."} style slips.
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


def locked_insight_payload(*, insights: Optional[list] = None) -> dict:
    return {"insights": insights_from(insights)}


def payload_from_parsed(data: dict) -> dict:
    if not isinstance(data, dict):
        return locked_insight_payload(insights=[])
    # Nested accidental wrap: {"result": {"insights": [...]}}
    if "insights" not in data:
        for key in ("result", "data", "insight_result", "output"):
            nested = data.get(key)
            if isinstance(nested, dict) and "insights" in nested:
                data = nested
                break
            if isinstance(nested, list):
                return locked_insight_payload(insights=nested)
    return locked_insight_payload(insights=data.get("insights"))


def parse_insight_json_content(content: Any) -> dict:
    if isinstance(content, dict):
        return payload_from_parsed(content)
    if isinstance(content, list):
        return locked_insight_payload(insights=content)

    text = normalize_json_text(content)
    if not text:
        return locked_insight_payload(insights=[])

    data = loads_json_object(text)
    if isinstance(data, dict):
        return payload_from_parsed(data)

    # Root JSON array is valid insight output; loads_json_object only returns objects.
    import json as _json

    try:
        raw = _json.loads(text)
    except _json.JSONDecodeError:
        raw = None
    if isinstance(raw, list):
        return locked_insight_payload(insights=raw)
    if isinstance(raw, dict):
        return payload_from_parsed(raw)

    # Model returned prose — split lightly into sentences as last resort.
    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return locked_insight_payload(insights=lines)
    return locked_insight_payload(insights=[text] if text else [])
