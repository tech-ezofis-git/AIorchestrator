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

    current_model = getattr(llm, "_model", None)
    overrides: dict[str, Any] = {}
    if model and model != current_model and not (
        current_model and current_model.endswith("/" + model)
    ):
        overrides["model"] = model

    result = await llm.chat_completion(
        [
            {"role": "system", "content": await rules.async_system_prompt()},
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
        ],
        **overrides,
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
