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
) -> dict[str, Any]:
    """Returns ocrResult, tableResult, usage, skill_id."""
    from app.core.response_composer import _parse_ocr_json_content

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
