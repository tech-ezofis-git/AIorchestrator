"""The Classification agent — document job (OCR text / file / filepath) → locked JSON."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.agents.reference_extraction import extract_reference
from app.classification_skills.classify_document import run as classify_document_skill
from app.config import Settings
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.response_composer import ResponseComposer
from app.integrations.ocr_engine import OcrEngineError
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import resolve_preset_overrides
from app.llm.runtime_models import RuntimeModelSelection

logger = logging.getLogger("orchestrator.classification_agent")

_SUCCESS_REPLY = "Document classification generated successfully."
_FAIL_REPLY = "I couldn't extract any text from that document, so I can't classify it."


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
        settings = self._cfg()
        model = (job.get("model") or "").strip() or None
        tenant_id = (job.get("tenant_id") or "").strip() or None

        content = ""
        source = "upload"
        page_label = ""

        if direct_text := (job.get("ocr_text") or "").strip():
            content = direct_text
            source = "ocr_text"
            page_label = "supplied text"
        else:
            try:
                pages = resolve_pageno(job.get("pageno"), max_pages=settings.ocr_max_pages)
            except InvalidOcrPageError as exc:
                raise ValueError(str(exc)) from exc

            filepath = (job.get("filepath") or "").strip() or None
            file_bytes = job.get("file_bytes")
            filename = job.get("filename")
            content_type = job.get("content_type")
            source = filepath or filename or "upload"
            page_label = pages.label()

            try:
                ocr_tool = await self._dispatcher.dispatch(
                    "run_ocr",
                    {
                        "reference": source,
                        "filepath": filepath,
                        "tenant_id": job.get("tenant_id"),
                        "filename": filename,
                        "content_type": content_type,
                        "file_bytes": file_bytes,
                        "page_start": pages.start,
                        "page_end": pages.end,
                        "page_raw": pages.raw,
                    },
                )
                content = (ocr_tool.get("text") or "").strip()
            except (ToolExecutionError, OcrEngineError, Exception) as exc:
                logger.warning(
                    "classification_document_extract_failed",
                    extra={"error_type": type(exc).__name__},
                )
                content = ""

        if not content:
            empty = await classify_document_skill(
                llm=self._llm_for_skill(),
                text="",
                source=source,
                page_label=page_label,
                tenant_id=tenant_id,
            )
            return _document_job_result(empty["payload"], source=source, usage=None)

        overrides = dict(job.get("llm_overrides") or {})
        fallback_overrides = job.get("llm_fallback_overrides")
        try:
            synthesis = await classify_document_skill(
                llm=self._llm_for_skill(),
                text=content,
                source=source,
                page_label=page_label,
                model=model,
                tenant_id=tenant_id,
                llm_overrides=overrides,
            )
        except Exception as exc:
            logger.warning(
                "classification_primary_failed",
                extra={"model": overrides.get("model") or model or "default"},
            )
            synthesis = await self._classify_with_fallback(
                text=content,
                source=source,
                page_label=page_label,
                primary=overrides.get("model") or model,
                error=exc,
                tenant_id=tenant_id,
                catalog_fallback_preset=job.get("catalog_fallback_preset"),
                fallback_overrides=fallback_overrides if isinstance(fallback_overrides, dict) else None,
            )

        usage = synthesis.get("usage") or {}
        return _document_job_result(
            synthesis["payload"],
            source=source,
            usage={
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            },
        )

    async def _classify_with_fallback(
        self,
        *,
        text: str,
        source: str,
        page_label: str,
        primary: Optional[str],
        error: Exception,
        tenant_id: Optional[str] = None,
        catalog_fallback_preset: Optional[str] = None,
        fallback_overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        settings = self._cfg()
        if not fallback_overrides:
            fallback_preset = catalog_fallback_preset or (
                self._runtime_models.fallback_preset_id if self._runtime_models else None
            )
            fallback_overrides = resolve_preset_overrides(fallback_preset) if fallback_preset else None
        env_fallback = (settings.ocr_fallback_model or "").strip() or None

        if fallback_overrides:
            logger.warning(
                "classification_fallback_preset",
                extra={"model": fallback_overrides.get("model")},
            )
            return await classify_document_skill(
                llm=self._llm_for_skill(),
                text=text,
                source=source,
                page_label=page_label,
                model=None,
                tenant_id=tenant_id,
                llm_overrides=fallback_overrides,
            )

        if env_fallback and env_fallback != primary:
            logger.warning("classification_fallback_model", extra={"model": env_fallback})
            return await classify_document_skill(
                llm=self._llm_for_skill(),
                text=text,
                source=source,
                page_label=page_label,
                model=env_fallback,
                tenant_id=tenant_id,
            )

        raise error


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
