"""The Prompt agent — skill pack + one user message, raw text back.

Not Chat: no EZOFIS chat persona, no session history, no memories.
System instructions come from `skills/prompt/` (SKILL.md + rules/*.mdc).
Does not parse or validate JSON even when the prompt asked for JSON.
`reply` is a status line; the model text lives in `prompt_result.text`.
"""
from __future__ import annotations

from typing import Any, Optional

from app.llm.adapter import LLMAdapter
from app.llm.model_presets import apply_preset, get_preset
from app.prompt_skills import rules as prompt_rules

_SUCCESS_REPLY = "Prompt executed successfully."


class PromptAgent:
    def __init__(self, llm_adapter: LLMAdapter):
        self._llm = llm_adapter

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        """Returns {"reply": str, "usage": dict | None, "prompt_result": dict}."""
        job = document_job or {}
        prompt = (job.get("prompt") or message or "").strip()
        if not prompt:
            raise ValueError("message is required for intent=prompt.")
        model = (job.get("model") or "").strip() or None

        previous = {
            "model": getattr(self._llm, "_model", None),
            "api_base": getattr(self._llm, "_api_base", None),
            "api_key": getattr(self._llm, "_api_key", None),
            "api_version": getattr(self._llm, "_api_version", None),
            "preset_id": getattr(self._llm, "_preset_id", None),
        }
        switched = False
        if model:
            if get_preset(model):
                apply_preset(self._llm, model)
                switched = True
            elif model != previous["model"] and not (
                previous["model"] and previous["model"].endswith("/" + model)
            ):
                self._llm.configure(model=model)
                switched = True

        try:
            result = await self._llm.chat_completion(
                [
                    {"role": "system", "content": prompt_rules.system_prompt()},
                    {"role": "user", "content": prompt},
                ]
            )
        finally:
            if switched:
                self._llm.configure(
                    model=previous["model"] or model,
                    api_base=previous["api_base"] if previous["api_base"] is not None else "",
                    api_key=previous["api_key"] if previous["api_key"] is not None else "",
                    api_version=(
                        previous["api_version"] if previous["api_version"] is not None else ""
                    ),
                    preset_id=(
                        previous["preset_id"] if previous["preset_id"] is not None else ""
                    ),
                )

        content = result.get("content")
        text = "" if content is None else str(content)
        return {
            "reply": _SUCCESS_REPLY,
            "usage": result.get("usage"),
            "prompt_result": {"text": text},
        }
