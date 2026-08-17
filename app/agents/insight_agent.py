"""Insight agent — flexible inputs → locked {insights: [...]} via skill pack.

Sources (document job), in precedence order:
  1. payload.insight_json  — arbitrary dashboard / report JSON
  2. payload.ocr_text      — pre-extracted text
  3. multipart file / filepath — OCR then insights

Legacy: free-text "insights on report RPT-…" still uses fetch_report_data
and returns reply + cited_data_points (unchanged for existing clients).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.agents.reference_extraction import extract_reference
from app.config import Settings
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.response_composer import ResponseComposer
from app.insight_skills.generate_insights import run as generate_insights_skill
from app.insight_skills.lock import locked_insight_payload
from app.insight_skills.rules import format_structured_payload
from app.integrations.ocr_engine import OcrEngineError
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import apply_preset, get_preset
from app.llm.runtime_models import RuntimeModelSelection

logger = logging.getLogger("orchestrator.insight_agent")

_SUCCESS_REPLY = "Insights generated successfully."
_FAIL_REPLY = "I couldn't find usable data to generate insights."


class InsightAgent:
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
        """Returns either legacy cited insights or locked insight_result."""
        if document_job:
            return await self._handle_document_job(document_job)

        report_id = extract_reference(message)
        report = await self._dispatcher.dispatch("fetch_report_data", {"report_id": report_id})
        synthesis = await self._response_composer.synthesize_insight(report=report)
        data_points = report.get("data_points") or []
        cited_data_points = [dp.get("label") for dp in data_points if dp.get("label")]
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "cited_data_points": cited_data_points,
        }

    async def _handle_document_job(self, job: dict[str, Any]) -> dict:
        insight_json = job.get("insight_json")
        if isinstance(insight_json, dict) and insight_json:
            content = format_structured_payload(insight_json)
            source = "insight_json"
            content_kind = "json"
        else:
            direct_text = (job.get("ocr_text") or "").strip()
            if direct_text:
                content = direct_text
                source = "ocr_text"
                content_kind = "text"
            else:
                settings = self._cfg()
                try:
                    pages = resolve_pageno(job.get("pageno"), max_pages=settings.ocr_max_pages)
                except InvalidOcrPageError as exc:
                    raise ValueError(str(exc)) from exc

                filepath = (job.get("filepath") or "").strip() or None
                file_bytes = job.get("file_bytes")
                filename = job.get("filename")
                content_type = job.get("content_type")
                source = filepath or filename or "upload"
                content = ""
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
                        "insight_document_extract_failed",
                        extra={"error_type": type(exc).__name__},
                    )
                    content = ""
                content_kind = "text"

        if not (content or "").strip():
            empty = locked_insight_payload(insights=[])
            return _job_result(empty, source=source, usage=None)

        model = (job.get("model") or "").strip() or None
        instruction = (job.get("instruction") or "").strip() or None
        try:
            synthesis = await generate_insights_skill(
                llm=self._llm_for_skill(),
                content=content,
                source=source,
                content_kind=content_kind,
                instruction=instruction,
                model=model,
            )
        except Exception as exc:
            logger.warning("insight_primary_failed", extra={"model": model or "default"})
            synthesis = await self._insight_with_fallback(
                content=content,
                source=source,
                content_kind=content_kind,
                instruction=instruction,
                primary=model,
                error=exc,
            )

        usage = synthesis.get("usage") or {}
        return _job_result(
            synthesis["payload"],
            source=source,
            usage={
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            },
        )

    async def _insight_with_fallback(
        self,
        *,
        content: str,
        source: str,
        content_kind: str,
        instruction: Optional[str],
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
                "insight_fallback_preset",
                extra={"fallback_preset_id": fallback_preset},
            )
            apply_preset(self._llm, fallback_preset)
            try:
                return await generate_insights_skill(
                    llm=self._llm_for_skill(),
                    content=content,
                    source=source,
                    content_kind=content_kind,
                    instruction=instruction,
                    model=None,
                )
            finally:
                if default_preset and get_preset(default_preset):
                    apply_preset(self._llm, default_preset)

        if env_fallback and env_fallback != primary:
            logger.warning("insight_fallback_model", extra={"model": env_fallback})
            return await generate_insights_skill(
                llm=self._llm_for_skill(),
                content=content,
                source=source,
                content_kind=content_kind,
                instruction=instruction,
                model=env_fallback,
            )

        raise error


def _job_result(
    payload: dict[str, Any],
    *,
    source: str,
    usage: Optional[dict[str, Any]],
) -> dict[str, Any]:
    body = dict(payload)
    body["source_reference"] = source
    insights = body.get("insights") or []
    return {
        "reply": _SUCCESS_REPLY if insights else _FAIL_REPLY,
        "usage": usage,
        "insight_result": body,
    }
