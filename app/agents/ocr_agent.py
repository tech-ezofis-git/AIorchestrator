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

logger = logging.getLogger("orchestrator.ocr_agent")


class OcrAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        response_composer: Optional[ResponseComposer] = None,
        settings: Optional[Settings] = None,
    ):
        self._dispatcher = dispatcher
        self._composer = response_composer
        self._settings = settings

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
                tokens=_zero_tokens(),
                ocr_text="",
            )
            body["source_reference"] = source
            body["ocr_status"] = ocr_status
            return {
                "reply": json.dumps(body, ensure_ascii=False),
                "usage": None,
                "ocr_result": body,
            }

        if self._composer is None:
            raise RuntimeError("ResponseComposer is required for OCR document jobs.")

        model = (job.get("model") or "").strip() or None
        primary = (
            model
            or (settings.ocr_default_model or "").strip()
            or settings.llm_model
        )
        fallback = (settings.ocr_fallback_model or "").strip() or primary

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
        except Exception:
            logger.warning("ocr_structuring_primary_failed", extra={"model": primary})
            if fallback == primary:
                raise
            synthesized = await self._composer.synthesize_ocr_json(
                instruction=instruction,
                ocr_text=ocr_text,
                parameters=parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                model=fallback,
                max_recommended_fields=settings.ocr_max_recommended_fields,
            )

        fields = synthesized["ocrResult"]
        table_result = synthesized.get("tableResult")
        usage = synthesized.get("usage") or {}
        tokens = {
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
            "cache_tokens": usage.get("cache_tokens") or 0,
        }
        body = _locked_body(ocr_result=fields, tokens=tokens, ocr_text=ocr_text, table_result=table_result)
        body["source_reference"] = source
        body["ocr_status"] = ocr_status
        if ocr_tool.get("mock"):
            body["mock"] = True

        return {
            "reply": json.dumps(
                {k: body[k] for k in ("ocrResult", "tokens", "ocr_text", "ocr_json") if k in body}
                | ({"tableResult": table_result} if table_result is not None else {}),
                ensure_ascii=False,
            ),
            "usage": {
                "prompt_tokens": tokens["prompt_tokens"],
                "completion_tokens": tokens["completion_tokens"],
                "total_tokens": tokens["total_tokens"],
            },
            "ocr_result": body,
        }


def _zero_tokens() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_tokens": 0,
    }


def _locked_body(
    *,
    ocr_result: list[dict[str, Any]],
    tokens: dict[str, int],
    ocr_text: str,
    table_result: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ocrResult": ocr_result,
        "tokens": tokens,
        "ocr_text": ocr_text,
        "ocr_json": {"ocrResult": ocr_result},
    }
    if table_result is not None:
        body["tableResult"] = table_result
        body["ocr_json"]["tableResult"] = table_result
    return body
