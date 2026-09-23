"""OCR text + tenant repo catalog → locked document_intelligent_result."""
from __future__ import annotations

from typing import Any, Optional

from app.document_intelligent_skills import rules
from app.document_intelligent_skills.lock import locked_payload, parse_json_content

SKILL_ID = "match_document_repository"


async def run(
    *,
    llm: Any,
    text: str,
    source: str,
    catalog: list[dict[str, Any]],
    page_label: str = "",
    model: Optional[str] = None,
    tenant_id: Optional[str] = None,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        return {
            "payload": locked_payload(ocr_text="", catalog=catalog),
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
                    catalog=catalog,
                ),
            },
        ],
        **overrides,
    )
    return {
        "payload": parse_json_content(result["content"], ocr_text=body, catalog=catalog),
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
