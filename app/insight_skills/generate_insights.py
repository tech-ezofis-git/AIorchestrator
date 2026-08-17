"""Reusable Insight skill: JSON or text → locked {insights: [...]}."""
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
) -> dict[str, Any]:
    """Returns {"payload": dict, "usage": dict | None, "skill_id": str}."""
    body = (content or "").strip()
    if not body:
        return {
            "payload": locked_insight_payload(insights=[]),
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

    payload = parse_insight_json_content(result["content"])
    return {
        "payload": payload,
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
