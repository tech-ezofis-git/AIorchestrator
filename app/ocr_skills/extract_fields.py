"""Reusable OCR skill: OCR text + parameters → ocrResult / tableResult.

Owned by the OCR agent path (not the orchestrator hallway). Applies OCR rules
around the LLM structuring call.
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.ocr_helpers import parse_parameter_entries
from app.ocr_skills import rules

SKILL_ID = "extract_fields"


async def run(
    *,
    llm: Any,
    instruction: str,
    ocr_text: str,
    parameters: list[str],
    tableparameters: list[str],
    page_label: str,
    model: Optional[str] = None,
    max_recommended_fields: int = 15,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Returns ocrResult, tableResult, usage, skill_id.

    `llm_overrides` (model/api_base/api_key/api_version) is passed straight
    into `llm.chat_completion(**overrides)` for THIS call only — it never
    mutates the shared `llm` adapter. `model` alone (no other override
    fields) is still accepted for back-compat call sites that only need to
    swap the model name against the adapter's current api_base/key.
    Previously this function called `llm.configure()` then restored it in
    a `finally` — unsafe under concurrency, since `llm` is one instance
    shared by every in-flight request (see app/llm/adapter.py's
    chat_completion docstring)."""
    from app.core.response_composer import _parse_ocr_json_content

    overrides = dict(llm_overrides or {})
    if model and "model" not in overrides:
        overrides["model"] = model

    parsed_params = parse_parameter_entries(parameters)
    param_lines = (
        "\n".join(f"- {n} ({t})" for n, t in parsed_params)
        if parsed_params
        else f"(none — recommend up to {max_recommended_fields} fields)"
    )
    table_lines = (
        "\n".join(f"- {p}" for p in tableparameters)
        if tableparameters
        else "(none)"
    )
    result = await llm.chat_completion(
        [
            {
                "role": "system",
                "content": rules.system_prompt(
                    max_recommended_fields=max_recommended_fields
                ),
            },
            {
                "role": "user",
                "content": rules.build_user_prompt(
                    instruction=instruction,
                    page_label=page_label,
                    param_lines=param_lines,
                    table_lines=table_lines,
                    ocr_text=ocr_text,
                ),
            },
        ],
        **overrides,
    )

    fields, table_result = _parse_ocr_json_content(
        result["content"],
        expected=parsed_params,
        max_recommended_fields=max_recommended_fields,
    )
    return {
        "ocrResult": fields,
        "tableResult": table_result,
        "usage": result.get("usage"),
        "skill_id": SKILL_ID,
    }
