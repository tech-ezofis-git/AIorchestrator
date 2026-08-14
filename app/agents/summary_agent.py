"""The Summary agent — legacy EZOFIS fetch, or document job (blob/upload
→ Paddle extract_text, or caller-supplied ocr_text) → locked summary JSON.
Same 3-model chain as OCR: payload.model → console/startup default → fallback.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.agents.reference_extraction import extract_reference
from app.config import Settings
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.response_composer import ResponseComposer
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
        direct_text = (job.get("ocr_text") or "").strip()
        if direct_text:
            ocr_text = direct_text
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

            ocr_text = ""
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
                ocr_text = (ocr_tool.get("text") or "").strip()
            except (ToolExecutionError, OcrEngineError, Exception) as exc:
                logger.warning(
                    "summary_document_extract_failed",
                    extra={"error_type": type(exc).__name__},
                )
                ocr_text = ""

        if not ocr_text:
            empty = await self._response_composer.synthesize_file_summary(
                text="",
                source=source,
                page_label=page_label,
            )
            return _document_job_result(empty["payload"], source=source, usage=None)

        model = (job.get("model") or "").strip() or None
        try:
            synthesis = await self._response_composer.synthesize_file_summary(
                text=ocr_text,
                source=source,
                page_label=page_label,
                model=model,
            )
        except Exception as exc:
            logger.warning("summary_primary_failed", extra={"model": model or "default"})
            synthesis = await self._summarize_with_fallback(
                text=ocr_text,
                source=source,
                page_label=page_label,
                primary=model,
                error=exc,
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
        primary: Optional[str],
        error: Exception,
    ) -> dict[str, Any]:
        settings = self._cfg()
        fallback_preset = (
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
                return await self._response_composer.synthesize_file_summary(
                    text=text,
                    source=source,
                    page_label=page_label,
                    model=None,
                )
            finally:
                if default_preset and get_preset(default_preset):
                    apply_preset(self._llm, default_preset)

        if env_fallback and env_fallback != primary:
            logger.warning("summary_fallback_model", extra={"model": env_fallback})
            return await self._response_composer.synthesize_file_summary(
                text=text,
                source=source,
                page_label=page_label,
                model=env_fallback,
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

    from app.core.response_composer import _payload_from_parsed

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
        from app.core.response_composer import _balance_json_text, _loads_json_object

        data = _loads_json_object(_balance_json_text(summary))
        if not isinstance(data, dict):
            return body
    if not isinstance(data, dict) or not data.get("document_summary"):
        return body

    fixed = _payload_from_parsed(data, ocr_text=str(body.get("ocr_text") or ""))
    if (fixed.get("document_summary") or "").strip().startswith("{"):
        return body
    return fixed
