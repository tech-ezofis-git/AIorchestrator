"""Pydantic models for the /chat endpoint."""
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field, model_validator


class DocumentPayload(BaseModel):
    """Document job fields for OCR and AP when intent is set explicitly."""

    filepath: Optional[str] = Field(
        default=None,
        description="Blob URL, or folder/file path inside container ezts{tenantid}. Ignored when a multipart file is uploaded.",
    )
    pageno: Optional[str] = Field(
        default=None,
        description="Page selector: omit/1..max for one page; -1 for pages 1..max.",
    )
    ocr_text: Optional[str] = Field(
        default=None,
        description=(
            "Pre-extracted OCR text. When set on intent=summary or insight, blob "
            "download and Paddle extract are skipped. Wins over file/filepath "
            "(insight_json still wins over ocr_text for insight)."
        ),
    )
    insight_json: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Arbitrary structured JSON for intent=insight (dashboard, report, "
            "metrics). Skips OCR. Wins over ocr_text / file / filepath."
        ),
    )
    parameters: list[str] = Field(default_factory=list)
    tableparameters: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant UUID. Required for relative blob filepath (container ezts{tenantid}).",
    )
    skills: Optional[list[str]] = Field(
        default=None,
        description="AP skills to run. Omitted/null => default pipeline (includes finalize_decision + workflow_move_next). List => only those ids.",
    )
    invoice_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Pre-extracted invoice JSON (skips OCR when provided).",
    )
    item_id: Optional[str] = Field(
        default=None,
        description="Stable AP document key for artifact re-runs. Defaults to filepath/filename/hash.",
    )
    repository_item_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_item_id", "repositoryItemId", "repositoryItemID"),
        description="Repository item UUID (move-next itemId). Alias: repositoryItemId.",
    )
    workflow_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
        description="AP workflow id (progress / move-next).",
    )
    instance_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("instance_id", "instanceId"),
        description="AP workflow instance id (progress/move-next).",
    )
    repository_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_id", "repositoryId", "repository"),
        description="Repository UUID for workflow move-next. Alias: repository, repositoryId.",
    )
    transaction_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("transaction_id", "transactionId"),
        description="Workflow transaction id for move-next.",
    )
    form_entry_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("form_entry_id", "formentryId", "formEntryId"),
        description="Form entry id (numeric or string) for move-next and ezfb row write-back.",
    )
    process_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("process_id", "processId"),
        description="Workflow process id for move-next.",
    )
    connector_id: Optional[str] = Field(default=None, description="QB/Sage connector id for PO lookup skills.")
    resource: Optional[str] = Field(
        default=None,
        description="PO resource hint: QUICKBOOKS or SAGE.",
    )
    matter_master_id: Optional[str] = Field(default=None, description="Matter master id for matter_validate.")
    form_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("form_id", "formid", "formId"),
        description="PO/document form id (GUID or numeric). Selects ezfb_{token}_items on the tenant DB.",
    )


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-supplied session identifier.")
    message: Optional[str] = Field(
        default=None,
        description=(
            "Free-text chat message. Optional when intent=ocr/summary/insight/ap "
            "with file, filepath, ocr_text, insight_json, or invoice_json."
        ),
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
        has_ocr_text = bool(payload and (payload.ocr_text or "").strip())
        has_insight_json = bool(payload and payload.insight_json)
        has_ap_doc = bool(
            payload
            and (
                payload.invoice_json
                or (payload.item_id or "").strip()
            )
        )
        msg = (self.message or "").strip()
        if (
            not msg
            and not has_filepath
            and not has_ocr_text
            and not has_insight_json
            and not has_ap_doc
        ):
            # Multipart uploads attach file bytes outside this model; main.py
            # validates file/filepath/ocr_text for intent=ocr/summary/insight/ap after parsing.
            if (self.intent or "").strip().lower() in {"ocr", "summary", "insight", "ap"}:
                return self
            raise ValueError(
                "Either message, payload.filepath, payload.ocr_text, "
                "payload.insight_json, or payload.invoice_json is required."
            )
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
    summary_result: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Summary document-job output — confidence_score, document_type, "
            "document_title, document_language, document_summary, "
            "key_facts_extracted, ocr_text (plus optional source_reference). "
            "`reply` is a short status line. Token counts live in token_usage."
        ),
    )
    insight_result: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Insight document-job output — locked {insights: [str, ...]} "
            "(plus optional source_reference). `reply` is a short status line. "
            "Legacy report-id insights still use reply + cited_data_points."
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
