"""OCR agent — legacy reference pass-through, or document job → extract_text → JSON."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.agents.ocr_helpers import (
    InvalidOcrPageError,
    parse_parameter_entries,
    resolve_instruction,
    resolve_pageno,
)
from app.agents.reference_extraction import extract_reference
from app.config import Settings
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.response_composer import ResponseComposer
from app.integrations.ocr_engine import OcrEngineError
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import resolve_preset_overrides
from app.llm.runtime_models import RuntimeModelSelection
from app.ocr_skills.extract_fields import run as extract_fields_skill

logger = logging.getLogger("orchestrator.ocr_agent")


class OcrAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        response_composer: Optional[ResponseComposer] = None,
        settings: Optional[Settings] = None,
        *,
        llm_adapter: Optional[LLMAdapter] = None,
        runtime_models: Optional[RuntimeModelSelection] = None,
        catalog_store: Any = None,
    ):
        self._dispatcher = dispatcher
        self._composer = response_composer
        self._settings = settings
        self._llm = llm_adapter
        self._runtime_models = runtime_models
        # Kept for constructor back-compat; catalog tenant/agent model
        # resolution now happens once, centrally, in app/main.py's chat()
        # handler (document_job["llm_overrides"]/["llm_fallback_overrides"])
        # rather than being re-resolved (and re-applied by mutation) here.
        self._catalog = catalog_store

    def _llm_for_skill(self) -> LLMAdapter:
        if self._llm is not None:
            return self._llm
        if self._composer is None:
            raise RuntimeError("LLM adapter is required for OCR document jobs.")
        return self._composer._llm

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

        # Legacy keyword path: reference from message, optional mock OCR, no LLM.
        reference = extract_reference(message)
        result = await self._dispatcher.dispatch("run_ocr", {"reference": reference})
        return {
            "reply": result["text"],
            "usage": None,
            "ocr_result": result,
        }

    async def _handle_document_job(self, job: dict[str, Any]) -> dict:
        settings = self._cfg()
        try:
            pages = resolve_pageno(job.get("pageno"), max_pages=settings.ocr_max_pages)
        except InvalidOcrPageError as exc:
            raise ValueError(str(exc)) from exc

        instruction = resolve_instruction(job.get("instruction"))
        parameters = list(job.get("parameters") or [])
        tableparameters = list(job.get("tableparameters") or [])
        filepath = (job.get("filepath") or "").strip() or None
        file_bytes = job.get("file_bytes")
        filename = job.get("filename")
        content_type = job.get("content_type")
        source = filepath or filename or "upload"

        ocr_status = "success"
        ocr_text = ""
        ocr_tool: dict[str, Any] = {}

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
            if not ocr_text:
                ocr_status = "fallback"
        except (ToolExecutionError, OcrEngineError, Exception) as exc:
            logger.warning(
                "ocr_document_extract_failed",
                extra={"error_type": type(exc).__name__},
            )
            ocr_status = "fallback"
            ocr_text = ""

        # No OCR text → do not hallucinate; null out requested fields.
        if not ocr_text:
            ocr_result_fields = [
                {"name": name, "value": None, "type": typ}
                for name, typ in parse_parameter_entries(parameters)
            ]
            body = _locked_body(
                ocr_result=ocr_result_fields,
                ocr_text="",
            )
            body["source_reference"] = source
            body["ocr_status"] = ocr_status
            return {
                "reply": json.dumps(_reply_payload(body), ensure_ascii=False),
                "usage": None,
                "ocr_result": body,
            }

        if self._composer is None and self._llm is None:
            raise RuntimeError("ResponseComposer or LLM adapter is required for OCR document jobs.")

        # Resolved once, up front, by app/main.py's chat() handler (explicit
        # payload.model, tenant/catalog selection, or a snapshot of the
        # adapter's current default) — passed straight into
        # chat_completion(**overrides) per call, never by mutating the
        # shared adapter (see app/llm/adapter.py's chat_completion
        # docstring for why that used to be unsafe under concurrency).
        overrides = dict(job.get("llm_overrides") or {})
        fallback_overrides = job.get("llm_fallback_overrides")

        try:
            synthesized = await extract_fields_skill(
                llm=self._llm_for_skill(),
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                max_recommended_fields=settings.ocr_max_recommended_fields,
                llm_overrides=overrides,
            )
        except Exception as exc:
            logger.warning(
                "ocr_structuring_primary_failed",
                extra={"model": overrides.get("model") or "default"},
            )
            synthesized = await self._structure_with_fallback(
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                primary_overrides=overrides,
                max_recommended_fields=settings.ocr_max_recommended_fields,
                error=exc,
                fallback_overrides=fallback_overrides,
            )

        fields = synthesized["ocrResult"]
        table_result = synthesized.get("tableResult")
        usage = synthesized.get("usage") or {}
        body = _locked_body(
            ocr_result=fields,
            ocr_text=ocr_text,
            table_result=table_result,
        )
        body["source_reference"] = source
        body["ocr_status"] = ocr_status
        if ocr_tool.get("mock"):
            body["mock"] = True

        return {
            "reply": json.dumps(_reply_payload(body), ensure_ascii=False),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            },
            "ocr_result": body,
        }

    async def _structure_with_fallback(
        self,
        *,
        instruction: str,
        ocr_text: str,
        parameters: list[str],
        tableparameters: list[str],
        page_label: str,
        primary_overrides: dict[str, Any],
        max_recommended_fields: int,
        error: Exception,
        fallback_overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Retry OCR structuring on the tenant/console/env fallback model.

        Every tier here is a plain overrides dict passed into
        `chat_completion(**overrides)` for that one retry call — none of
        this mutates the shared adapter (see app/llm/adapter.py)."""
        settings = self._cfg()
        if not fallback_overrides:
            console_fallback = (
                self._runtime_models.fallback_preset_id if self._runtime_models else None
            )
            fallback_overrides = resolve_preset_overrides(console_fallback) if console_fallback else None

        if fallback_overrides:
            logger.warning(
                "ocr_structuring_fallback_preset",
                extra={"model": fallback_overrides.get("model")},
            )
            return await extract_fields_skill(
                llm=self._llm_for_skill(),
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=page_label,
                max_recommended_fields=max_recommended_fields,
                llm_overrides=fallback_overrides,
            )

        env_fallback = (settings.ocr_fallback_model or "").strip() or None
        primary_model = primary_overrides.get("model")
        if env_fallback and env_fallback != primary_model:
            logger.warning(
                "ocr_structuring_fallback_model",
                extra={"model": env_fallback},
            )
            # Keep the primary call's api_base/api_key/api_version (if any
            # were explicitly resolved) and only swap the model name.
            env_overrides = {**primary_overrides, "model": env_fallback}
            return await extract_fields_skill(
                llm=self._llm_for_skill(),
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=page_label,
                max_recommended_fields=max_recommended_fields,
                llm_overrides=env_overrides,
            )

        raise error


def _locked_body(
    *,
    ocr_result: list[dict[str, Any]],
    ocr_text: str,
    table_result: Any = None,
) -> dict[str, Any]:
    """Single OCR payload node: fields + tables + text (no nested duplicates)."""
    return {
        "ocrResult": ocr_result,
        "tableResult": table_result if table_result is not None else [],
        "ocr_text": ocr_text,
    }


def _reply_payload(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "ocrResult": body.get("ocrResult") or [],
        "tableResult": body.get("tableResult") or [],
        "ocr_text": body.get("ocr_text") or "",
    }
