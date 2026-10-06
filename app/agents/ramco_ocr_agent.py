"""Ramco OCR agent — File Fetcher / OCR / Prompt Builder (skills/ramco_ocr) / LLM Reasoner → final invoice JSON."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.agents.ocr_helpers import resolve_pageno
from app.classification_skills.contract import numeric_id
from app.config import Settings, get_settings
from app.core.dispatcher import Dispatcher
from app.llm.adapter import LLMAdapter
from app.tools.pipeline import AgentProfile, DispatcherOcrEngine, register_profile, run_agent_pipeline

logger = logging.getLogger("orchestrator.ramco_ocr_agent")

AGENT_NAME = "RAMCO_OCR"
CONTRACT_AGENT = "OCR"
SKILL_AGENT = "ramco_ocr"
EMPTY_ERROR = "OCR extraction returned empty values."
PARSE_ERROR = "LLM response was not a valid JSON object."
USER_TEMPLATE = "Invoice Text:\n{text}"


def build_user_prompt(ocr_text: str) -> str:
    return USER_TEMPLATE.format(text=ocr_text)


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


def _pipeline_error(result: dict[str, Any]) -> Optional[str]:
    step = result.get("failed_step")
    error = result.get("error")
    if step is None:
        return None
    if step == "file_fetcher":
        return f"File fetch failed: {error}"
    if step == "prompt_builder":
        return f"Prompt preparation failed: {error}"
    if step == "llm_reasoner" and not (error or "").startswith("LLM call failed"):
        return PARSE_ERROR
    return error


def adapt_pipeline_result(result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Generic pipeline result → ramco_ocr_result contract response."""
    error = _pipeline_error(result)
    parsed: Optional[dict[str, Any]] = None
    if error is None:
        parsed = result["reasoning"].get("json")
        if not isinstance(parsed, dict):
            parsed, error = None, PARSE_ERROR
    if error:
        logger.warning("ramco_ocr_pipeline_failed", extra={"step": result.get("failed_step") or "parse"})

    contract = build_contract(document_job=job, parsed=parsed, error=error)
    if contract["ERROR CODE"]:
        reply = f"OCR extraction failed: {contract['ERROR CODE']}"
    else:
        reply = f"Extracted {len(contract['extraction']['lineItems'])} line item(s) from the invoice."
    return {
        "reply": reply,
        "usage": result.get("usage"),
        "document_id": contract["source"]["blobPath"],
        "ramco_ocr_result": contract,
    }


register_profile(AgentProfile(intent=SKILL_AGENT, adapter=adapt_pipeline_result, user_template=USER_TEMPLATE))


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
        if not (job.get("ocr_text") or "").strip():
            resolve_pageno(job.get("pageno"), max_pages=self._cfg().ocr_max_pages)
        return await run_agent_pipeline(
            SKILL_AGENT,
            job,
            ocr_engine=DispatcherOcrEngine(self._dispatcher),
            llm=self._llm,
            llm_overrides=job.get("llm_overrides"),
        )

