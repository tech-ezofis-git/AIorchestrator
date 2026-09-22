"""Reusable Classification skill: source text → locked classification_result JSON."""
from __future__ import annotations

from typing import Any, Optional

from app.classification_skills import rules
from app.classification_skills.lock import locked_classification_payload, parse_classification_json_content

SKILL_ID = "classify_document"


async def run(
    *,
    llm: Any,
    text: str,
    source: str,
    page_label: str = "",
    model: Optional[str] = None,
    tenant_id: Optional[str] = None,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Returns {"payload": dict, "usage": dict | None, "skill_id": str}."""
    body = (text or "").strip()
    if not body:
        return {
            "payload": locked_classification_payload(ocr_text=""),
            "usage": None,
            "skill_id": SKILL_ID,
        }

    overrides = dict(llm_overrides or {})
    if model and "model" not in overrides:
        overrides["model"] = model

    result = await llm.chat_completion(
        [
            {"role": "system", "content": await rules.async_system_prompt(tenant_id=tenant_id)},
            {
                "role": "user",
                "content": rules.build_user_prompt(
                    source=source,
                    page_label=page_label,
                    content=body,
                ),
            },
        ],
        **overrides,
    )

    payload = parse_classification_json_content(result["content"], ocr_text=body)
    return {
        "payload": payload,
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
