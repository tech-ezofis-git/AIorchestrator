"""Ramco OCR agent — file upload / blob path → Paddle OCR → skills/ramco_ocr prompt → final invoice JSON."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.agent_skills.loader import get_skill
from app.agents.ocr_helpers import resolve_pageno
from app.classification_skills.contract import numeric_id
from app.config import Settings, get_settings
from app.core.dispatcher import Dispatcher
from app.llm.adapter import LLMAdapter
from app.summary_skills.lock import loads_json_object

logger = logging.getLogger("orchestrator.ramco_ocr_agent")

AGENT_NAME = "RAMCO_OCR"
CONTRACT_AGENT = "OCR"
SKILL_AGENT = "ramco_ocr"
EMPTY_ERROR = "OCR extraction returned empty values."


class RamcoOcrError(Exception):
    """A pipeline step failed; `stage` says which (ocr / llm / parse)."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def system_prompt(settings: Optional[Settings] = None) -> str:
    """skills/ramco_ocr SKILL.md + rules/*.mdc from disk."""
    return get_skill(SKILL_AGENT, settings=settings).system_prompt


def build_user_prompt(ocr_text: str) -> str:
    return f"Invoice Text:\n{ocr_text}"


def _confidence(parsed: dict[str, Any], header: dict[str, Any]) -> Optional[float]:
    raw = parsed.get("confidence", header.pop("confidence", None))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 1.0:
        value = value / 100.0
    return round(max(0.0, min(1.0, value)), 2)


def _has_values(header: dict[str, Any], line_items: list[Any]) -> bool:
    if any(v not in (None, "") for v in header.values()):
        return True
    return any(
        isinstance(item, dict) and any(v not in (None, "") for k, v in item.items() if k != "lineNo")
        for item in line_items
    )


def build_contract(
    *,
    document_job: dict[str, Any],
    parsed: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Client-facing OCR contract (Extraction Status / extraction / ERROR CODE)."""
    if threshold is None:
        threshold = get_settings().ramco_ocr_min_confidence
    header: Optional[dict[str, Any]] = None
    line_items: list[Any] = []
    confidence: Optional[float] = None

    if error is None and parsed is not None:
        raw_header = parsed.get("invoice_header")
        header = dict(raw_header) if isinstance(raw_header, dict) else {}
        raw_items = parsed.get("line_items")
        line_items = raw_items if isinstance(raw_items, list) else []
        confidence = _confidence(parsed, header)
        if not _has_values(header, line_items):
            error = EMPTY_ERROR
        elif confidence is not None and confidence < threshold:
            error = f"Extraction confidence too low ({confidence:.2f} < {threshold:.2f})."

    if error is not None:
        header, line_items = None, []
        if confidence is None:
            confidence = 0.0

    return {
        "agent": CONTRACT_AGENT,
        "Extraction Status": "SUCCEEDED" if error is None else "FAILED",
        "documentType": document_job.get("document_type") or "UNKNOWN",
        "Extraction Completed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "blobPath": document_job.get("filepath")
            or document_job.get("filename")
            or ("ocr_text" if (document_job.get("ocr_text") or "").strip() else None)
        },
        "environment": {
            "envType": document_job.get("env_type"),
            "tenantId": numeric_id(document_job.get("tenant_id")),
            "workflowId": numeric_id(document_job.get("workflow_id")),
            "repositoryId": numeric_id(document_job.get("repository_id")),
            "instanceId": document_job.get("instance_id"),
        },
        "extraction": {
            "model": document_job.get("model_display"),
            "confidence": confidence,
            "invoiceHeader": header,
            "lineItems": line_items,
        },
        "ERROR CODE": error,
    }


class RamcoOcrAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        llm: LLMAdapter,
        settings: Optional[Settings] = None,
    ):
        self._dispatcher = dispatcher
        self._llm = llm
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    async def extract_text(
        self,
        *,
        filepath: Optional[str],
        file_bytes: Optional[bytes],
        filename: Optional[str],
        content_type: Optional[str],
        tenant_id: Optional[str],
        pageno: Optional[str],
    ) -> str:
        pages = resolve_pageno(pageno, max_pages=self._cfg().ocr_max_pages)
        source = filepath or filename or "upload"
        try:
            ocr = await self._dispatcher.dispatch(
                "run_ocr",
                {
                    "reference": source,
                    "filepath": filepath,
                    "tenant_id": tenant_id,
                    "filename": filename,
                    "content_type": content_type,
                    "file_bytes": file_bytes,
                    "page_start": pages.start,
                    "page_end": pages.end,
                    "page_raw": pages.raw,
                    "layout": True,
                },
            )
        except Exception as exc:
            logger.warning("ramco_ocr_extract_failed", extra={"error_type": type(exc).__name__})
            raise RamcoOcrError("ocr", f"OCR failed: {exc}") from exc
        text = (ocr.get("text") or "") if isinstance(ocr, dict) else ""
        if not text.strip():
            raise RamcoOcrError("ocr", "No text could be extracted from the document.")
        return text

    async def parse_invoice(
        self,
        ocr_text: str,
        *,
        llm_overrides: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        """Returns (final STAGE 2 invoice JSON, token usage)."""
        try:
            result = await self._llm.chat_completion(
                [
                    {"role": "system", "content": system_prompt(self._settings)},
                    {"role": "user", "content": build_user_prompt(ocr_text)},
                ],
                **(llm_overrides or {}),
            )
        except Exception as exc:
            logger.warning("ramco_ocr_llm_failed", extra={"error_type": type(exc).__name__})
            raise RamcoOcrError("llm", f"LLM call failed: {exc}") from exc

        parsed = loads_json_object(result.get("content"))
        if not isinstance(parsed, dict):
            raise RamcoOcrError("parse", "LLM response was not a valid JSON object.")
        return parsed, result.get("usage")

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """POST /chat intent=ramco_ocr. Pipeline failures come back as a FAILED contract."""
        job = document_job or {}
        usage: Optional[dict[str, Any]] = None
        parsed: Optional[dict[str, Any]] = None
        error: Optional[str] = None
        try:
            ocr_text = job.get("ocr_text") or ""
            if not ocr_text.strip():
                ocr_text = await self.extract_text(
                    filepath=job.get("filepath"),
                    file_bytes=job.get("file_bytes"),
                    filename=job.get("filename"),
                    content_type=job.get("content_type"),
                    tenant_id=job.get("tenant_id"),
                    pageno=job.get("pageno"),
                )
            parsed, usage = await self.parse_invoice(ocr_text, llm_overrides=job.get("llm_overrides"))
        except RamcoOcrError as exc:
            error = str(exc)
        except ValueError:
            raise
        except Exception as exc:
            logger.exception("ramco_ocr_unexpected_error")
            error = f"OCR extraction failed: {exc}"

        contract = build_contract(document_job=job, parsed=parsed, error=error)
        if contract["ERROR CODE"]:
            reply = f"OCR extraction failed: {contract['ERROR CODE']}"
        else:
            reply = f"Extracted {len(contract['extraction']['lineItems'])} line item(s) from the invoice."
        return {
            "reply": reply,
            "usage": usage,
            "document_id": contract["source"]["blobPath"],
            "ramco_ocr_result": contract,
        }
