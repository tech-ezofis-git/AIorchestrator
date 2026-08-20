"""The Summary agent — legacy EZOFIS fetch, or document job (JSON blob / OCR text /
file / filepath → locked summary JSON). Same 3-model chain as OCR.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.agents.reference_extraction import extract_reference
from app.config import Settings
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.response_composer import ResponseComposer
from app.summary_skills import rules
from app.summary_skills.lock import (
    balance_json_text,
    loads_json_object,
    payload_from_parsed,
)
from app.summary_skills.summarize_document import run as summarize_document_skill
from app.integrations.ocr_engine import OcrEngineError
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import apply_preset, get_preset
from app.llm.runtime_models import RuntimeModelSelection

logger = logging.getLogger("orchestrator.summary_agent")

_SUCCESS_REPLY = "Document summary generated successfully."
_FAIL_REPLY = "I couldn't extract any text from that document, so I can't summarize it."


class SummaryAgent:
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
        """Returns {"reply": str, "usage": dict | None, "document_id": str,
        "summary_result": dict | None}."""
        if document_job:
            return await self._handle_document_job(document_job)

        document_id = extract_reference(message)
        document = await self._dispatcher.dispatch("fetch_document", {"document_id": document_id})
        synthesis = await self._response_composer.synthesize_summary(document=document)
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "document_id": document_id,
        }

    async def _handle_document_job(self, job: dict[str, Any]) -> dict:
        settings = self._cfg()
        summary_json = job.get("summary_json")
        key_facts_count = rules.resolve_key_facts_count(
            explicit=job.get("key_facts_count"),
            summary_json=summary_json if isinstance(summary_json, dict) else None,
        )
        model = (job.get("model") or "").strip() or None
        tenant_id = (job.get("tenant_id") or "").strip() or None

        content = ""
        source = "upload"
        content_kind = "text"
        source_text = ""
        page_label = ""

        if isinstance(summary_json, dict) and summary_json:
            data = rules.strip_summary_control_keys(summary_json)
            if data:
                content = rules.format_structured_payload(data)
                source = "summary_json"
                content_kind = "json"
                source_text = content
        elif direct_text := (job.get("ocr_text") or "").strip():
            content = direct_text
            source = "ocr_text"
            content_kind = "text"
            source_text = direct_text
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
                source_text = content
            except (ToolExecutionError, OcrEngineError, Exception) as exc:
                logger.warning(
                    "summary_document_extract_failed",
                    extra={"error_type": type(exc).__name__},
                )
                content = ""
                source_text = ""

        if not content:
            empty = await summarize_document_skill(
                llm=self._llm_for_skill(),
                text="",
                source=source,
                page_label=page_label,
                key_facts_count=key_facts_count,
                tenant_id=tenant_id,
            )
            return _document_job_result(empty["payload"], source=source, usage=None)

        try:
            synthesis = await summarize_document_skill(
                llm=self._llm_for_skill(),
                text=content,
                source=source,
                page_label=page_label,
                model=model,
                content_kind=content_kind,
                source_text=source_text,
                key_facts_count=key_facts_count,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.warning("summary_primary_failed", extra={"model": model or "default"})
            synthesis = await self._summarize_with_fallback(
                text=content,
                source=source,
                page_label=page_label,
                content_kind=content_kind,
                source_text=source_text,
                key_facts_count=key_facts_count,
                primary=model,
                error=exc,
                tenant_id=tenant_id,
                catalog_fallback_preset=job.get("catalog_fallback_preset"),
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

    async def _summarize_with_fallback(
        self,
        *,
        text: str,
        source: str,
        page_label: str,
        content_kind: str,
        source_text: str,
        key_facts_count: int,
        primary: Optional[str],
        error: Exception,
        tenant_id: Optional[str] = None,
        catalog_fallback_preset: Optional[str] = None,
    ) -> dict[str, Any]:
        settings = self._cfg()
        fallback_preset = catalog_fallback_preset or (
            self._runtime_models.fallback_preset_id if self._runtime_models else None
        )
        env_fallback = (settings.ocr_fallback_model or "").strip() or None

        if fallback_preset and self._llm is not None and get_preset(fallback_preset):
            default_preset = (
                self._runtime_models.default_preset_id if self._runtime_models else None
            )
            logger.warning(
                "summary_fallback_preset",
                extra={"fallback_preset_id": fallback_preset},
            )
            apply_preset(self._llm, fallback_preset)
            try:
                return await summarize_document_skill(
                    llm=self._llm_for_skill(),
                    text=text,
                    source=source,
                    page_label=page_label,
                    model=None,
                    content_kind=content_kind,
                    source_text=source_text,
                    key_facts_count=key_facts_count,
                    tenant_id=tenant_id,
                )
            finally:
                if default_preset and get_preset(default_preset):
                    apply_preset(self._llm, default_preset)

        if env_fallback and env_fallback != primary:
            logger.warning("summary_fallback_model", extra={"model": env_fallback})
            return await summarize_document_skill(
                llm=self._llm_for_skill(),
                text=text,
                source=source,
                page_label=page_label,
                model=env_fallback,
                content_kind=content_kind,
                source_text=source_text,
                key_facts_count=key_facts_count,
                tenant_id=tenant_id,
            )

        raise error


def _document_job_result(
    payload: dict[str, Any],
    *,
    source: str,
    usage: Optional[dict[str, Any]],
) -> dict[str, Any]:
    body = _unwrap_summary_result(dict(payload))
    body["source_reference"] = source
    has_text = bool((body.get("ocr_text") or "").strip())
    return {
        "reply": _SUCCESS_REPLY if has_text else _FAIL_REPLY,
        "usage": usage,
        "document_id": source,
        "summary_result": body,
    }


def _unwrap_summary_result(body: dict[str, Any]) -> dict[str, Any]:
    """If the model stuffed the whole JSON object into document_summary, unpack it."""
    import json

    summary = str(body.get("document_summary") or "").strip()
    facts = body.get("key_facts_extracted")
    already_unpacked = isinstance(facts, list) and len(facts) > 0
    looks_like_json = summary.startswith("{") and '"document_summary"' in summary
    if already_unpacked and not looks_like_json:
        return body
    if not looks_like_json:
        return body

    try:
        data = json.loads(summary)
    except json.JSONDecodeError:
        data = loads_json_object(balance_json_text(summary))
        if not isinstance(data, dict):
            return body
    if not isinstance(data, dict) or not data.get("document_summary"):
        return body

    fixed = payload_from_parsed(data, ocr_text=str(body.get("ocr_text") or ""))
    if (fixed.get("document_summary") or "").strip().startswith("{"):
        return body
    return fixed
