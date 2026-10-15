"""Pydantic models for the Generic Form-to-Master AI Agent."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FormSyncRequest(BaseModel):
    source_form_id: str = Field(
        ...,
        description="Source Workflow Form ID or prefix (e.g. 'aa038e45-b62f-4b4d-8dd8-638c75c3f447' or 'aa038e45')"
    )
    target_form_id: str = Field(
        ...,
        description="Target Master Form ID or prefix (e.g. 'f271d1d3-a0ac-4747-a9f4-09aab4214a88' or 'f271d1d3')"
    )
    payload: Dict[str, Any] = Field(
        ...,
        description="The submitted workflow form data payload (ticket attributes and component slots)"
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="Optional tenant UUID. If omitted, uses active tenant or parses from DATABASE_URL"
    )
    dry_run: Optional[bool] = Field(
        default=False,
        description="If True, performs schema mapping and AI reasoning without writing to PostgreSQL"
    )
    model: Optional[str] = Field(
        default=None,
        description="Optional LLM model override"
    )


class TargetRecordPlan(BaseModel):
    match_criteria: Dict[str, Any] = Field(
        ...,
        description="Primary key or unique column identifiers to lookup existing target record (e.g. {'Material_Code': 'BTL-005'})"
    )
    fields: Dict[str, Any] = Field(
        ...,
        description="Key-value pairs to insert or update in the target master table"
    )
    action: Optional[str] = Field(
        default=None,
        description="Resulting DB action: 'inserted', 'updated', or 'skipped'"
    )
    target_id: Optional[str] = Field(
        default=None,
        description="UUID of the target record in PostgreSQL"
    )


class FormSyncResponse(BaseModel):
    success: bool
    source_record_id: Optional[str] = None
    target_records_count: int = 0
    target_records: List[Dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    message: str = ""
    reasoning: Optional[str] = None
