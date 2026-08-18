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
