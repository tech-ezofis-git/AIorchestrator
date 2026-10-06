"""The Classification agent — document job (OCR text / file / filepath) → locked JSON."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.agents.reference_extraction import extract_reference
from app.classification_skills.classify_document import run as classify_document_skill
from app.classification_skills.lock import locked_classification_payload, parse_classification_json_content
from app.classification_skills.rules import USER_PROMPT_PREFIX
from app.config import Settings
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import resolve_preset_overrides
from app.llm.runtime_models import RuntimeModelSelection
from app.tools.pipeline import AgentProfile, DispatcherOcrEngine, register_profile, run_agent_pipeline

logger = logging.getLogger("orchestrator.classification_agent")

INTENT = "classification"
_SUCCESS_REPLY = "Document classification generated successfully."
_FAIL_REPLY = "I couldn't extract any text from that document, so I can't classify it."
USER_TEMPLATE = f"{USER_PROMPT_PREFIX}\n\nSource: {{source}}{{page_suffix}}\n\nOCR text:\n{{text}}"


def adapt_pipeline_result(result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Generic pipeline result → classification_result response (same shape as before)."""
    source = result.get("source") or "upload"
    step = result.get("failed_step")
    if step in {"input", "file_fetcher", "ocr"} or not (result.get("ocr_text") or "").strip():
        if step:
            logger.warning("classification_document_extract_failed", extra={"step": step})
        return _document_job_result(locked_classification_payload(ocr_text=""), source=source, usage=None)
    if step:
        raise RuntimeError(result.get("error") or f"{step} failed.")
    payload = parse_classification_json_content(result["reasoning"]["content"], ocr_text=result["ocr_text"])
    usage = result.get("usage") or {}
    return _document_job_result(
        payload,
        source=source,
        usage={
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        },
    )


register_profile(AgentProfile(intent=INTENT, adapter=adapt_pipeline_result, user_template=USER_TEMPLATE))


class ClassificationAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        response_composer: ResponseComposer,
        settings: Optional[Settings] = None,
        *,
        llm_adapter: Optional[LLMAdapter] = None,
        runtime_models: Optional[RuntimeModelSelection] = None,
    ):
        self._dispatcher = dispatcher
        self._response_composer = response_composer
        self._settings = settings
        self._llm = llm_adapter
        self._runtime_models = runtime_models

    def _llm_for_skill(self) -> LLMAdapter:
        if self._llm is not None:
            return self._llm
        return self._response_composer._llm

    def _cfg(self) -> Settings:
        if self._settings is None:
            from app.config import get_settings

            return get_settings()
        return self._settings

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        if document_job:
            return await self._handle_document_job(document_job)

        document_id = extract_reference(message)
        document = await self._dispatcher.dispatch("fetch_document", {"document_id": document_id})
        content = (document.get("content") or "").strip()
        synthesis = await classify_document_skill(
            llm=self._llm_for_skill(),
            text=content,
            source=document_id,
            page_label="",
        )
        usage = synthesis.get("usage") or {}
        return _document_job_result(
            synthesis["payload"],
            source=document_id,
            usage={
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            },
        )

    async def _handle_document_job(self, job: dict[str, Any]) -> dict:
        """File Fetcher / OCR / Prompt Builder / LLM Reasoner, skipping steps the input already covers."""
        if not (job.get("ocr_text") or "").strip():
            try:
                resolve_pageno(job.get("pageno"), max_pages=self._cfg().ocr_max_pages)
            except InvalidOcrPageError as exc:
                raise ValueError(str(exc)) from exc

        model = (job.get("model") or "").strip() or None
        overrides = dict(job.get("llm_overrides") or {})
        if model and "model" not in overrides:
            overrides["model"] = model
        return await run_agent_pipeline(
            INTENT,
            job,
            ocr_engine=DispatcherOcrEngine(self._dispatcher),
            llm=self._llm_for_skill(),
            llm_overrides=overrides,
            fallback_overrides=self._fallback_candidates(job, primary=overrides.get("model")),
        )

    def _fallback_candidates(self, job: dict[str, Any], *, primary: Optional[str]) -> list[dict[str, Any]]:
        """Fallback LLM settings, in the same order the agent always used: catalog/runtime preset, then env model."""
        fallback_overrides = job.get("llm_fallback_overrides")
        if not isinstance(fallback_overrides, dict) or not fallback_overrides:
            fallback_preset = job.get("catalog_fallback_preset") or (
                self._runtime_models.fallback_preset_id if self._runtime_models else None
            )
            fallback_overrides = resolve_preset_overrides(fallback_preset) if fallback_preset else None
        if fallback_overrides:
            return [fallback_overrides]
        env_fallback = (self._cfg().ocr_fallback_model or "").strip() or None
        if env_fallback and env_fallback != primary:
            return [{"model": env_fallback}]
        return []


def _document_job_result(
    payload: dict[str, Any],
    *,
    source: str,
    usage: Optional[dict[str, Any]],
) -> dict[str, Any]:
    body = dict(payload)
    body["source_reference"] = source
    has_text = bool((body.get("ocr_text") or "").strip())
    return {
        "reply": _SUCCESS_REPLY if has_text else _FAIL_REPLY,
        "usage": usage,
        "document_id": source,
        "classification_result": body,
    }
