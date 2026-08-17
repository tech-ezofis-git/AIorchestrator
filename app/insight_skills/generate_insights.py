"""Reusable Insight skill: JSON or text → locked insight_result JSON."""
from __future__ import annotations

from typing import Any, Optional

from app.insight_skills import rules
from app.insight_skills.lock import locked_insight_payload, parse_insight_json_content

SKILL_ID = "generate_insights"


async def run(
    *,
    llm: Any,
    content: str,
    source: str,
    content_kind: str = "text",
    instruction: Optional[str] = None,
    model: Optional[str] = None,
    insights_count: int = rules.DEFAULT_INSIGHTS_COUNT,
    insight_area: Optional[str] = None,
    source_text: str = "",
) -> dict[str, Any]:
    """Returns {"payload": dict, "usage": dict | None, "skill_id": str}."""
    body = (content or "").strip()
    count = rules.resolve_insights_count(explicit=insights_count)
    area = rules.resolve_insight_area(explicit=insight_area)
    stored_source = (source_text or body).strip()
    if not body:
        return {
            "payload": locked_insight_payload(
                insights=[],
                insights_count=count,
                insight_area=area,
                source_text=stored_source,
            ),
            "usage": None,
            "skill_id": SKILL_ID,
        }

    previous = {
        "model": getattr(llm, "_model", None),
        "api_base": getattr(llm, "_api_base", None),
        "api_key": getattr(llm, "_api_key", None),
        "api_version": getattr(llm, "_api_version", None),
        "preset_id": getattr(llm, "_preset_id", None),
    }
    switched = False
    if model and model != previous["model"] and not (
        previous["model"] and previous["model"].endswith("/" + model)
    ):
        llm.configure(model=model)
        switched = True

    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": rules.system_prompt()},
                {
                    "role": "user",
                    "content": rules.build_user_prompt(
                        source=source,
                        content=body,
                        content_kind=content_kind,
                        instruction=instruction,
                        insights_count=count,
                        insight_area=area,
                    ),
                },
            ]
        )
    finally:
        if switched:
            llm.configure(
                model=previous["model"] or model,
                api_base=previous["api_base"] if previous["api_base"] is not None else "",
                api_key=previous["api_key"] if previous["api_key"] is not None else "",
                api_version=previous["api_version"] if previous["api_version"] is not None else "",
                preset_id=previous["preset_id"] if previous["preset_id"] is not None else "",
            )

    payload = parse_insight_json_content(
        result["content"],
        insights_count=count,
        insight_area=area,
        source_text=stored_source,
    )
    return {
        "payload": payload,
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
