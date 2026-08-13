"""Pydantic models for the /chat endpoint."""
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class DocumentPayload(BaseModel):
    """Document job fields for OCR and AP when intent is set explicitly."""

    filepath: Optional[str] = Field(
        default=None,
        description="Blob URL or container/blob path (\\ or /). Ignored when a multipart file is uploaded.",
    )
    pageno: Optional[str] = Field(
        default=None,
        description="Page selector: omit/1..max for one page; -1 for pages 1..max.",
    )
    parameters: list[str] = Field(default_factory=list)
    tableparameters: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    tenant_id: Optional[str] = Field(
        default=None,
        description="AP tenant id (intent=ap). Defaults to 'default' when omitted.",
    )
    skills: Optional[list[str]] = Field(
        default=None,
        description="AP skills to run. Omitted => tenant default plan. Re-run a subset using stored artifacts.",
    )
    invoice_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Pre-extracted invoice JSON (skips OCR when provided).",
    )
    item_id: Optional[str] = Field(
        default=None,
        description="Stable AP document key for artifact re-runs. Defaults to filepath/filename/hash.",
    )
    workflow_id: Optional[str] = Field(default=None, description="AP workflow id (progress skill).")
    instance_id: Optional[str] = Field(default=None, description="AP workflow instance id (progress/move-next).")
    connector_id: Optional[str] = Field(default=None, description="QB/Sage connector id for PO lookup skills.")
    resource: Optional[str] = Field(
        default=None,
        description="PO resource hint: QUICKBOOKS or SAGE.",
    )
    matter_master_id: Optional[str] = Field(default=None, description="Matter master id for matter_validate.")


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-supplied session identifier.")
    message: Optional[str] = Field(
        default=None,
        description="Free-text chat message. Optional when intent=ocr|ap with file/filepath/invoice_json.",
    )
    intent: Optional[str] = Field(
        default=None,
        description="Explicit agent. Empty/omitted => chat. Unknown values rejected by the route.",
    )
    instruction: Optional[str] = Field(
        default=None,
        description="OCR structuring hints (region/date format). Optional.",
    )
    payload: Optional[DocumentPayload] = None

    @model_validator(mode="after")
    def _require_message_or_document(self) -> "ChatRequest":
        payload = self.payload
        has_filepath = bool(payload and (payload.filepath or "").strip())
        has_ap_doc = bool(
            payload
            and (
                payload.invoice_json
                or (payload.item_id or "").strip()
            )
        )
        msg = (self.message or "").strip()
        if not msg and not has_filepath and not has_ap_doc:
            # Multipart uploads attach file bytes outside this model; main.py
            # validates file-or-filepath for intent=ocr|ap after parsing.
            if (self.intent or "").strip().lower() in ("ocr", "ap"):
                return self
            raise ValueError("Either message or payload.filepath is required.")
        return self


class TokenUsage(BaseModel):
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    correlation_id: str
    latency_ms: float
    token_usage: Optional[TokenUsage] = None
    chunk_ids: Optional[list[str]] = Field(
        default=None,
        description="Chunk IDs the reply was synthesized from (Search intent only; absent/None otherwise).",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Source document id the reply was synthesized from (Summary intent only; absent/None otherwise).",
    )
    cited_data_points: Optional[list[str]] = Field(
        default=None,
        description="Report data point labels the reply was synthesized from (Insight intent only; absent/None otherwise).",
    )
    ocr_result: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "OCR intent output — ocrResult + tableResult + ocr_text "
            "(plus optional source_reference / ocr_status). Token counts live in token_usage."
        ),
    )
    forecast_result: Optional[dict] = Field(
        default=None,
        description=(
            "Raw forecast tool output (metric, horizon, predicted_values, confidence_interval) — "
            "Forecast intent only; absent/None otherwise. `reply` carries the narrated explanation."
        ),
    )
    invoice_reference: Optional[str] = Field(
        default=None,
        description=(
            "Invoice reference the reply was synthesized from (AP intent only; absent/None otherwise). "
            "Also None when AP couldn't identify a confident reference and returned a clarification instead."
        ),
    )
    mail_draft: Optional[dict] = Field(
        default=None,
        description=(
            "Full drafted email (action_id, recipient, subject, body) — Mail intent only; absent/None "
            "otherwise, and also None when Mail couldn't identify a valid recipient and returned a "
            "clarification instead. Nothing is sent until POST /actions/{action_id}/confirm is called."
        ),
    )
    ap_result: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "AP document-job output (run_id, skills_run, credits_charged, decision, artifacts) — "
            "intent=ap with file/filepath/invoice_json only; absent/None for legacy invoice-status Q&A."
        ),
    )
