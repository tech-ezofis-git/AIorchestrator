"""Pydantic models for the /chat endpoint."""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-supplied session identifier.")
    message: str = Field(..., min_length=1, description="The user's chat message.")


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
    ocr_result: Optional[dict] = Field(
        default=None,
        description=(
            "Full structured OCR tool output (text, confidence, source_reference) — "
            "OCR intent only; absent/None otherwise. `reply` also carries the extracted text."
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
