"""OCR agent — legacy reference pass-through, or document job → extract_text → JSON."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from datetime import timezone as dt_timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from app.integrations.mrz_parse import apply_mrz_to_fields, find_mrz, mrz_document_kind
from app.integrations.ocr_engine import OcrEngineError
from app.llm.adapter import LLMAdapter
from app.llm.model_presets import resolve_preset_overrides
from app.llm.runtime_models import RuntimeModelSelection
from app.ocr_skills.expiry_status import (
    apply_expiry_status,
    document_expiry,
    fill_status_field,
    is_expiry_field,
    same_field_name,
)
from app.ocr_skills.extract_fields import run as extract_fields_skill

logger = logging.getLogger("orchestrator.ocr_agent")

# Asked of the model, never returned, when a status field is requested without an expiry field.
_HIDDEN_EXPIRY_FIELD = "Expiry Date"


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
        qr_codes: list[dict[str, Any]] = []
        image_mrz: Optional[dict[str, Any]] = None

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
                    "scan_qr": True,
                    "scan_mrz": settings.ocr_mrz_enabled,
                },
            )
            ocr_text = (ocr_tool.get("text") or "").strip()
            qr_codes = list(ocr_tool.get("qr_codes") or [])
            image_mrz = ocr_tool.get("mrz")
            if not ocr_text:
                ocr_status = "fallback"
        except (ToolExecutionError, OcrEngineError, Exception) as exc:
            logger.warning(
                "ocr_document_extract_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            ocr_status = "fallback"
            ocr_text = ""

        mrz = _pick_mrz(image_mrz, find_mrz(ocr_text)) if settings.ocr_mrz_enabled else None
        model_text = _with_mrz_text(_with_qr_text(ocr_text, qr_codes), mrz)

        # No OCR text or QR data → do not hallucinate; null out requested fields.
        if not model_text:
            ocr_result_fields = [
                {"name": name, "value": None, "type": typ}
                for name, typ in parse_parameter_entries(parameters)
            ]
            body = _locked_body(
                ocr_result=ocr_result_fields,
                ocr_text="",
                qr_codes=qr_codes,
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

        status_name = (settings.ocr_expiry_field_name or "").strip()
        requested = [name for name, _ in parse_parameter_entries(parameters)]
        fill_status = bool(status_name) and any(same_field_name(n, status_name) for n in requested)
        hide_expiry = fill_status and not any(is_expiry_field(n) for n in requested)
        llm_parameters = [*parameters, f"{_HIDDEN_EXPIRY_FIELD},DATE"] if hide_expiry else parameters

        try:
            synthesized = await extract_fields_skill(
                llm=self._llm_for_skill(),
                instruction=instruction,
                ocr_text=model_text,
                parameters=llm_parameters,
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
                ocr_text=model_text,
                parameters=llm_parameters,
                tableparameters=tableparameters,
                page_label=pages.label(),
                primary_overrides=overrides,
                max_recommended_fields=settings.ocr_max_recommended_fields,
                error=exc,
                fallback_overrides=fallback_overrides,
            )

        today = _today(settings.ocr_expiry_timezone)
        fields = apply_mrz_to_fields(synthesized["ocrResult"], mrz)
        if fill_status:
            expiry = document_expiry(fields, mrz)
            if hide_expiry:
                fields = [f for f in fields if not same_field_name(f.get("name"), _HIDDEN_EXPIRY_FIELD)]
            fields = fill_status_field(apply_expiry_status(fields, today=today), name=status_name, expiry=expiry, today=today)
        else:
            fields = apply_expiry_status(fields, today=today, rename_to=status_name or None)
        table_result = synthesized.get("tableResult")
        usage = synthesized.get("usage") or {}
        body = _locked_body(
            ocr_result=fields,
            ocr_text=ocr_text,
            table_result=table_result,
            qr_codes=qr_codes,
            mrz=mrz,
            document_type=mrz_document_kind(mrz) or synthesized.get("documentType"),
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


def _today(timezone: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("ocr_expiry_timezone_invalid", extra={"timezone": timezone})
        return datetime.now(dt_timezone.utc).date()


def _with_qr_text(ocr_text: str, qr_codes: list[dict[str, Any]]) -> str:
    """OCR text plus a QR section, so field structuring sees both sources."""
    if not qr_codes:
        return ocr_text
    lines = ["--- QR codes found in the document ---"]
    for qr in qr_codes:
        content = qr.get("decoded") or qr.get("data")
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        lines.append(f"QR (page {qr.get('page')}): {content}")
    return "\n\n".join(part for part in (ocr_text, "\n".join(lines)) if part)


def _pick_mrz(
    image_mrz: Optional[dict[str, Any]], text_mrz: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Image-read MRZ first; the MRZ found in the extracted text is the fallback."""
    candidates = [m for m in (image_mrz, text_mrz and {**text_mrz, "source": "text"}) if m]
    if not candidates:
        return None
    return max(candidates, key=lambda m: (bool(m.get("valid")), sum((m.get("checks") or {}).values())))


def _with_mrz_text(text: str, mrz: Optional[dict[str, Any]]) -> str:
    """Appends the decoded MRZ so field structuring can use (and prefer) its values."""
    if not mrz:
        return text
    skip = ("checks", "raw_lines", "valid", "source", "page")
    fields = {k: v for k, v in mrz.items() if k not in skip and v}
    status = (
        "all check digits valid - prefer the document number and dates over the printed text; "
        "names carry no check digit, so prefer the printed names when they differ"
        if mrz.get("valid")
        else "some check digits failed: " + ", ".join(k for k, ok in mrz["checks"].items() if not ok)
    )
    block = "\n".join(
        [f"--- MRZ found in the document (decoded; {status}) ---"]
        + [f"{key}: {value}" for key, value in fields.items()]
    )
    return "\n\n".join(part for part in (text, block) if part)


def _locked_body(
    *,
    ocr_result: list[dict[str, Any]],
    ocr_text: str,
    table_result: Any = None,
    qr_codes: Optional[list[dict[str, Any]]] = None,
    mrz: Optional[dict[str, Any]] = None,
    document_type: Optional[str] = None,
) -> dict[str, Any]:
    """Single OCR payload node: document type + fields + tables + QR codes + MRZ + text (no nested duplicates)."""
    return {
        "document_type": document_type,
        "ocrResult": ocr_result,
        "tableResult": table_result if table_result is not None else [],
        "qr_codes": qr_codes or [],
        "mrz": mrz,
        "ocr_text": ocr_text,
    }


def _reply_payload(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_type": body.get("document_type"),
        "ocrResult": body.get("ocrResult") or [],
        "tableResult": body.get("tableResult") or [],
        "qr_codes": body.get("qr_codes") or [],
        "mrz": body.get("mrz"),
        "ocr_text": body.get("ocr_text") or "",
    }
