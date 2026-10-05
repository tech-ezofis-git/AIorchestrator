"""CLASSIFICATION response contract for POST /chat intent=classification.

Turns the agent's locked classification_result (confidence_score 0–100,
document_type, …) into the client-facing shape: status, completion time,
source, environment, classification {model, documentType, confidence 0–1},
ocr_text (raw extracted text, unchanged), and ERROR CODE.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import get_settings

AGENT_NAME = "CLASSIFICATION"
UNKNOWN_TYPE = "UNKNOWN"
BLANK_TYPE = "BLANK"
NO_TEXT_ERROR = "No text could be extracted from the document."
BLANK_ERROR = "Document is blank or unreadable."
NO_INVOICE_ERROR = "No invoice detected in the document."
# Only these labels count as a successful classification; every other
# allowed label is reported as FAILED with NO_INVOICE_ERROR.
INVOICE_TYPES = frozenset({"INVOICE"})
# Labels defined by skills/classification (client-approved prompt).
ALLOWED_LABELS = frozenset(
    {"INVOICE", "CREDIT_NOTE", "STATEMENT", "TERMS", "BOL", "PACKING_LIST", "BLANK", "OTHER"}
)
_VENDOR_PREFIXES = ("azure openai", "openai", "azure")


def model_id_from_display_name(name: Optional[str]) -> Optional[str]:
    """'OpenAI GPT-4.1' -> 'gpt-4.1'; 'GPT-4.1 Mini' -> 'gpt-4.1-mini'."""
    text = (name or "").strip().lower()
    if not text:
        return None
    for prefix in _VENDOR_PREFIXES:
        if text.startswith(prefix + " "):
            text = text[len(prefix):].strip()
            break
    return re.sub(r"\s+", "-", text) or None


def normalize_document_type(value: Optional[str]) -> str:
    """'Purchase Order' -> 'PURCHASE_ORDER'; empty -> 'UNKNOWN'."""
    slug = re.sub(r"[^A-Z0-9]+", "_", (value or "").strip().upper()).strip("_")
    return slug or UNKNOWN_TYPE


def numeric_id(value: Any) -> Any:
    """'11' -> 11; UUIDs and other non-numeric ids pass through unchanged."""
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _outcome(
    classification_result: Optional[dict[str, Any]],
    error: Optional[str],
    threshold: float,
) -> tuple[str, float, Optional[str]]:
    if error is not None:
        return UNKNOWN_TYPE, 0.0, error
    body = classification_result or {}
    if not (body.get("ocr_text") or "").strip():
        return UNKNOWN_TYPE, 0.0, NO_TEXT_ERROR
    confidence = round(float(body.get("confidence_score") or 0.0) / 100.0, 2)
    document_type = normalize_document_type(body.get("document_type"))
    if document_type == UNKNOWN_TYPE:
        return UNKNOWN_TYPE, confidence, "Document type could not be determined."
    if document_type not in ALLOWED_LABELS:
        return UNKNOWN_TYPE, confidence, f"Classifier returned an unsupported label: {document_type}."
    if document_type == BLANK_TYPE:
        return BLANK_TYPE, confidence, BLANK_ERROR
    if confidence < threshold:
        return (
            UNKNOWN_TYPE,
            confidence,
            "Document type could not be determined with enough confidence "
            f"({confidence:.2f} < {threshold:.2f}).",
        )
    if document_type not in INVOICE_TYPES:
        return document_type, confidence, f"{NO_INVOICE_ERROR} Detected: {document_type}."
    return document_type, confidence, None


def build_contract(
    *,
    blob_path: Optional[str],
    model: Optional[str],
    env_type: Optional[str],
    tenant_id: Any,
    workflow_id: Any,
    repository_id: Any,
    instance_id: Optional[str],
    classification_result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    threshold: Optional[float] = None,
) -> dict[str, Any]:
    if threshold is None:
        threshold = get_settings().classification_min_confidence
    document_type, confidence, error = _outcome(classification_result, error, threshold)
    ocr_text = (classification_result or {}).get("ocr_text") or ""
    return {
        "agent": AGENT_NAME,
        "Classification Status": "SUCCEEDED" if error is None else "FAILED",
        "Classification Completed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"blobPath": blob_path},
        "environment": {
            "envType": env_type,
            "tenantId": numeric_id(tenant_id),
            "workflowId": numeric_id(workflow_id),
            "repositoryId": numeric_id(repository_id),
            "instanceId": instance_id,
        },
        "classification": {
            "model": model,
            "documentType": document_type,
            "confidence": confidence,
        },
        "ocr_text": ocr_text,
        "ERROR CODE": error,
    }


def with_contract(
    result: dict[str, Any],
    document_payload: Optional[Any],
    document_job: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Replace an agent result's classification_result with the contract.

    `result` may carry `classification_error` (agent raised) instead of a
    classification_result; it is consumed here and never returned.
    """
    p = document_payload
    job = document_job or {}
    blob_path = (p.filepath if p else None) or job.get("filename") or result.get("document_id")
    contract = build_contract(
        blob_path=blob_path,
        model=p.model if p else None,
        env_type=p.env_type if p else None,
        tenant_id=p.tenant_id if p else None,
        workflow_id=p.workflow_id if p else None,
        repository_id=p.repository_id if p else None,
        instance_id=p.instance_id if p else None,
        classification_result=result.get("classification_result"),
        error=result.get("classification_error"),
    )
    updated = {k: v for k, v in result.items() if k != "classification_error"}
    updated["classification_result"] = contract
    updated["document_id"] = result.get("document_id") or blob_path
    if contract["ERROR CODE"]:
        updated["reply"] = f"Document classification failed: {contract['ERROR CODE']}"
    return updated
