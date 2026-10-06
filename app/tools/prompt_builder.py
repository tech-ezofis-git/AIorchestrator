"""Prompt Builder tool — intent + OCR text → skill/rules system prompt + user message, no LLM call."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agent_packs.overlay import get_agent_skill_with_source
from app.agent_skills.types import LoadedSkill
from app.config import Settings

logger = logging.getLogger("orchestrator.tools.prompt_builder")

TOOL_ID = "prompt_builder"
NO_INTENT_ERROR = "An intent is required to pick the skill pack."
NO_TEXT_ERROR = "OCR text is required."


def _result(
    *,
    status: str,
    intent: Optional[str],
    skill: Optional[dict[str, Any]] = None,
    messages: Optional[list[dict[str, str]]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    messages = messages or []
    return {
        "tool": TOOL_ID,
        "status": status,
        "intent": intent,
        "skill": skill,
        "messages": messages,
        "chars": sum(len(m["content"]) for m in messages),
        "error": error,
    }


async def _load_skill(
    intent: str, *, tenant_id: Optional[str], settings: Optional[Settings]
) -> tuple[LoadedSkill, str]:
    """Skill packs use either `snake_case` or `kebab-case` folder names."""
    candidates = [intent]
    if "_" in intent:
        candidates.append(intent.replace("_", "-"))
    last_error: Optional[Exception] = None
    for name in candidates:
        try:
            return await get_agent_skill_with_source(name, tenant_id=tenant_id, settings=settings)
        except FileNotFoundError as exc:
            last_error = exc
    raise last_error or FileNotFoundError(intent)


def build_user_message(
    ocr_text: str,
    instruction: Optional[str] = None,
    *,
    template: Optional[str] = None,
    variables: Optional[dict[str, Any]] = None,
) -> str:
    """Default: optional "Instruction:" block + "Document Text:" block.

    With `template`, `{text}` and `{instruction}` plus any `variables` keys are filled in.
    """
    if template:
        values = {"instruction": (instruction or "").strip(), **(variables or {}), "text": ocr_text}
        return template.format_map(_Blank(values))
    parts = []
    if instruction and instruction.strip():
        parts.append(f"Instruction:\n{instruction.strip()}")
    parts.append(f"Document Text:\n{ocr_text}")
    return "\n\n".join(parts)


class _Blank(dict):
    """Unknown template placeholders render as empty strings instead of raising."""

    def __missing__(self, key: str) -> str:
        return ""


async def build_llm_prompt(
    intent: str,
    ocr_text: str,
    *,
    instruction: Optional[str] = None,
    tenant_id: Optional[str] = None,
    user_template: Optional[str] = None,
    template_vars: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Load the intent's SKILL.md + rules (Catalog DB first, disk fallback) and pair them with the OCR text.

    Returns `messages` ready for `run_llm_reasoning`. Never raises: a missing intent,
    empty OCR text, or unknown skill pack comes back as status FAILED with `error` set.
    OCR text is passed through unchanged so layout spacing reaches the model.
    `user_template` (e.g. "Invoice Text:\\n{text}") lets each caller shape the user message.
    """
    name = (intent or "").strip().lower()
    if not name:
        return _result(status="FAILED", intent=None, error=NO_INTENT_ERROR)
    if not (ocr_text or "").strip():
        return _result(status="FAILED", intent=name, error=NO_TEXT_ERROR)

    tid = (tenant_id or "").strip() or None
    try:
        skill, source = await _load_skill(name, tenant_id=tid, settings=settings)
    except FileNotFoundError:
        return _result(status="FAILED", intent=name, error=f"No skill pack found for intent '{name}'.")
    except Exception as exc:
        logger.warning("prompt_builder_skill_failed", extra={"intent": name, "error_type": type(exc).__name__})
        return _result(status="FAILED", intent=name, error=f"Could not load skill pack: {exc}")

    system_prompt = skill.system_prompt
    if not system_prompt.strip():
        return _result(status="FAILED", intent=name, error=f"Skill pack for intent '{name}' is empty.")

    skill_info = {
        "id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "rules": [r.description.strip() or r.path.stem for r in skill.rules if r.always_apply],
        "source": source,
        "tenant_id": tid,
    }
    user_message = build_user_message(ocr_text, instruction, template=user_template, variables=template_vars)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    return _result(status="SUCCEEDED", intent=name, skill=skill_info, messages=messages)
