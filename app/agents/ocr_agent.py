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
from app.llm.model_presets import apply_preset, get_preset
from app.llm.runtime_models import RuntimeModelSelection

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
    ):
        self._dispatcher = dispatcher
        self._composer = response_composer
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

        if self._composer is None:
            raise RuntimeError("ResponseComposer is required for OCR document jobs.")

        model = (job.get("model") or "").strip() or None
        # Prefer explicit payload.model; otherwise keep the shared adapter
        # (default preset chosen in the Test Console / startup).
        primary = model

        try:
            synthesized = await self._composer.synthesize_ocr_json(
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                model=primary,
                max_recommended_fields=settings.ocr_max_recommended_fields,
            )
        except Exception as exc:
            logger.warning("ocr_structuring_primary_failed", extra={"model": primary or "default"})
            synthesized = await self._structure_with_fallback(
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                primary=primary,
                max_recommended_fields=settings.ocr_max_recommended_fields,
                error=exc,
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
        primary: Optional[str],
        max_recommended_fields: int,
        error: Exception,
    ) -> dict[str, Any]:
        """Retry OCR structuring on the console/env fallback model."""
        assert self._composer is not None
        settings = self._cfg()
        fallback_preset = (
            self._runtime_models.fallback_preset_id if self._runtime_models else None
        )
        env_fallback = (settings.ocr_fallback_model or "").strip() or None

        if fallback_preset and self._llm is not None and get_preset(fallback_preset):
            default_preset = (
                self._runtime_models.default_preset_id
                if self._runtime_models
                else None
            )
            logger.warning(
                "ocr_structuring_fallback_preset",
                extra={"fallback_preset_id": fallback_preset},
            )
            apply_preset(self._llm, fallback_preset)
            try:
                return await self._composer.synthesize_ocr_json(
                    instruction=instruction,
                    ocr_text=ocr_text,
                    parameters=parameters,
                    tableparameters=tableparameters,
                    page_label=page_label,
                    model=None,
                    max_recommended_fields=max_recommended_fields,
                )
            finally:
                if default_preset and get_preset(default_preset):
                    apply_preset(self._llm, default_preset)

        if env_fallback and env_fallback != primary:
            logger.warning(
                "ocr_structuring_fallback_model",
                extra={"model": env_fallback},
            )
            return await self._composer.synthesize_ocr_json(
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=page_label,
                model=env_fallback,
                max_recommended_fields=max_recommended_fields,
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
