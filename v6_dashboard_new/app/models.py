"""Pydantic models for /chat and /dashboard/* APIs."""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


def unwrap_dashboard_json(value: Any) -> Any:
    """Accept dashboard_result, a ChatResponse envelope, or a JSON string."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return value
    if not isinstance(value, dict):
        return value
    inner = value.get("dashboard_result")
    top_kpis = value.get("kpis")
    if isinstance(inner, dict) and isinstance(inner.get("kpis"), list) and not isinstance(top_kpis, list):
        return inner
    return value


def _copy_if_missing(dest: dict[str, Any], source: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if dest.get(key) in (None, "") and source.get(key) not in (None, ""):
            dest[key] = source[key]


class DashboardPayload(BaseModel):
    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant UUID.",
        validation_alias=AliasChoices("tenant_id", "tenantId"),
    )
    repository_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_id", "repositoryId", "repository"),
    )
    workflow_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
    )
    dashboard_json: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("dashboard_json", "dashboardJson"),
        description="Edited schema from call 1. When set, /chat hydrates enabled widgets.",
    )

    @field_validator("dashboard_json", mode="before")
    @classmethod
    def _unwrap_chat_dashboard_json(cls, value: Any) -> Any:
        return unwrap_dashboard_json(value)


class ChatRequest(BaseModel):
    session_id: str = Field(validation_alias=AliasChoices("session_id", "sessionId"))
    message: Optional[str] = None
    intent: Optional[str] = Field(default="dashboard")
    payload: Optional[DashboardPayload] = None

    @model_validator(mode="after")
    def _intent_must_be_dashboard(self) -> "ChatRequest":
        intent = (self.intent or "dashboard").strip().lower()
        if intent and intent != "dashboard":
            raise ValueError('This service only supports intent="dashboard".')
        self.intent = "dashboard"
        return self


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    correlation_id: str
    latency_ms: float
    dashboard_result: Optional[dict[str, Any]] = None
    html: Optional[str] = Field(
        default=None,
        description="Always null on /dashboard/schema. /chat data may include HTML. /dashboard/data returns text/html instead.",
    )


class DashboardSchemaRequest(BaseModel):
    """POST /dashboard/schema — propose widgets. tenant_id plus repository_id or workflow_id."""

    session_id: str = Field(default="demo", validation_alias=AliasChoices("session_id", "sessionId"))
    message: Optional[str] = None
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenant_id", "tenantId"),
    )
    repository_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_id", "repositoryId", "repository"),
    )
    workflow_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
    )
    payload: Optional[DashboardPayload] = None

    @model_validator(mode="before")
    @classmethod
    def _lift_chat_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nested = data.get("payload")
        if isinstance(nested, dict):
            _copy_if_missing(
                data,
                nested,
                ("tenant_id", "tenantId", "repository_id", "repositoryId", "repository", "workflow_id", "workflowId"),
            )
        return data

    @model_validator(mode="after")
    def _apply_payload(self) -> "DashboardSchemaRequest":
        body = self.payload
        if body:
            self.tenant_id = self.tenant_id or body.tenant_id
            self.repository_id = self.repository_id or body.repository_id
            self.workflow_id = self.workflow_id or body.workflow_id
        return self


class DashboardDataRequest(BaseModel):
    """POST /dashboard/data — hydrate edited schema JSON."""

    session_id: str = Field(default="demo", validation_alias=AliasChoices("session_id", "sessionId"))
    message: Optional[str] = "apply"
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenant_id", "tenantId"),
    )
    repository_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_id", "repositoryId", "repository"),
    )
    workflow_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
    )
    dashboard_json: dict[str, Any] = Field(
        validation_alias=AliasChoices("dashboard_json", "dashboardJson"),
        description="Edited dashboard_result from POST /dashboard/schema.",
    )
    payload: Optional[DashboardPayload] = None

    @model_validator(mode="before")
    @classmethod
    def _lift_chat_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nested = data.get("payload")
        if isinstance(nested, dict):
            _copy_if_missing(
                data,
                nested,
                (
                    "tenant_id",
                    "tenantId",
                    "repository_id",
                    "repositoryId",
                    "repository",
                    "workflow_id",
                    "workflowId",
                    "dashboard_json",
                    "dashboardJson",
                ),
            )
        if "dashboard_json" not in data and "dashboardJson" not in data:
            envelope = data.get("dashboard_result")
            if isinstance(envelope, dict):
                data["dashboard_json"] = envelope
        data["dashboard_json"] = unwrap_dashboard_json(
            data.get("dashboard_json") if "dashboard_json" in data else data.get("dashboardJson")
        )
        return data

    @model_validator(mode="after")
    def _apply_payload(self) -> "DashboardDataRequest":
        body = self.payload
        if body:
            self.tenant_id = self.tenant_id or body.tenant_id
            self.repository_id = self.repository_id or body.repository_id
            self.workflow_id = self.workflow_id or body.workflow_id
            if body.dashboard_json and not self.dashboard_json:
                self.dashboard_json = body.dashboard_json
        return self


class PromptRequest(BaseModel):
    """POST /prompts — suggest a dashboard message from the items table."""

    session_id: str = Field(default="demo", validation_alias=AliasChoices("session_id", "sessionId"))
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenant_id", "tenantId"),
    )
    repository_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_id", "repositoryId", "repository"),
    )
    workflow_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
    )
    repository_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repository_name", "repositoryName"),
    )
    workflow_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflow_name", "workflowName"),
    )
    payload: Optional[DashboardPayload] = None

    @model_validator(mode="before")
    @classmethod
    def _lift_chat_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nested = data.get("payload")
        if isinstance(nested, dict):
            _copy_if_missing(
                data,
                nested,
                (
                    "tenant_id",
                    "tenantId",
                    "repository_id",
                    "repositoryId",
                    "repository",
                    "workflow_id",
                    "workflowId",
                    "repository_name",
                    "repositoryName",
                    "workflow_name",
                    "workflowName",
                ),
            )
        return data

    @model_validator(mode="after")
    def _apply_payload(self) -> "PromptRequest":
        body = self.payload
        if body:
            self.tenant_id = self.tenant_id or body.tenant_id
            self.repository_id = self.repository_id or body.repository_id
            self.workflow_id = self.workflow_id or body.workflow_id
        return self


class PromptResponse(BaseModel):
    session_id: str
    prompt: str
    correlation_id: str
    latency_ms: float
    tenant_id: Optional[str] = None
    repository_id: Optional[str] = None
    repository_name: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_name: Optional[str] = None
    table: Optional[str] = None
