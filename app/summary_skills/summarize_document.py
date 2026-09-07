"""Reusable Summary skill: source text or JSON → locked summary_result JSON.

Owned by the Summary agent path (not the orchestrator hallway). Applies
Summary rules (prompt + highlight lock) around the LLM call.
"""
from __future__ import annotations

from typing import Any, Optional

from app.summary_skills import rules
from app.summary_skills.lock import locked_summary_payload, parse_summary_json_content

SKILL_ID = "summarize_document"


async def run(
    *,
    llm: Any,
    text: str,
    source: str,
    page_label: str = "",
    model: Optional[str] = None,
    content_kind: str = "text",
    source_text: Optional[str] = None,
    key_facts_count: int = rules.DEFAULT_KEY_FACTS_COUNT,
    tenant_id: Optional[str] = None,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Returns {"payload": dict, "usage": dict | None, "skill_id": str}."""
    body = (text or "").strip()
    stored_source = (source_text if source_text is not None else body).strip()
    count = rules.resolve_key_facts_count(explicit=key_facts_count)
    if not body:
        return {
            "payload": locked_summary_payload(ocr_text="", key_facts_count=count),
            "usage": None,
            "skill_id": SKILL_ID,
        }

    overrides = dict(llm_overrides or {})
    if model and "model" not in overrides:
        overrides["model"] = model

    result = await llm.chat_completion(
        [
            {"role": "system", "content": rules.system_prompt(tenant_id=tenant_id)},
            {
                "role": "user",
                "content": rules.build_user_prompt(
                    source=source,
                    page_label=page_label,
                    content=body,
                    content_kind=content_kind,
                    key_facts_count=count,
                ),
            },
        ],
        **overrides,
    )

    payload = parse_summary_json_content(
        result["content"],
        ocr_text=stored_source,
        key_facts_count=count,
    )
    return {
        "payload": payload,
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
