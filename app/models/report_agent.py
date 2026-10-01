"""Pydantic models for the rebuilt Report Agent (Phase 1 prompt + Phase 2 run)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ReportTypeInfo(BaseModel):
    key: str
    name: str
    description: str
    scope: str  # workflows | repositories
    required_params: list[str] = Field(default_factory=list, serialization_alias="requiredParams")
    example_description: Optional[str] = Field(default=None, serialization_alias="exampleDescription")


class ScopeOption(BaseModel):
    id: str
    name: str


class SchemaColumnSlice(BaseModel):
    name: str
    type: str


class SchemaTableSlice(BaseModel):
    schema_name: Optional[str] = Field(default=None, serialization_alias="schemaName")
    table: str
    columns: list[SchemaColumnSlice] = Field(default_factory=list)


class GeneratePromptRequest(BaseModel):
    report_type: str = Field(
        ...,
        validation_alias=AliasChoices("reportType", "report_type", "type"),
    )
    description: str = Field(default="")
    workflow_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflowName", "workflow_name"),
    )
    repository_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repositoryName", "repository_name"),
    )
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenantId", "tenant_id", "TenantId"),
    )
    model: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class GeneratePromptResponse(BaseModel):
    report_type: str = Field(..., serialization_alias="reportType")
    title: str
    report_prompt: str = Field(..., serialization_alias="reportPrompt")
    warnings: list[str] = Field(default_factory=list)
    schema_tables: list[str] = Field(default_factory=list, serialization_alias="schemaTables")
    duration_ms: Optional[float] = Field(default=None, serialization_alias="durationMs")

    model_config = ConfigDict(populate_by_name=True)


class ReportSortSpec(BaseModel):
    field: str
    direction: str = "asc"


class RunReportRequest(BaseModel):
    report_prompt: str = Field(
        ...,
        validation_alias=AliasChoices("reportPrompt", "report_prompt", "prompt"),
    )
    report_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("reportType", "report_type", "type"),
    )
    workflow_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflowName", "workflow_name"),
    )
    repository_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repositoryName", "repository_name"),
    )
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenantId", "tenant_id", "TenantId"),
    )
    filters: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(
        default=25,
        ge=1,
        le=500,
        validation_alias=AliasChoices("pageSize", "page_size", "limit"),
    )
    sort: Optional[ReportSortSpec] = None
    model: Optional[str] = None
    include_debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("includeDebug", "include_debug"),
    )

    model_config = ConfigDict(populate_by_name=True)


class ReportColumnOut(BaseModel):
    key: str
    label: str


class AvailableFilterOut(BaseModel):
    key: str
    label: str
    field: Optional[str] = None
    type: str = "text"


class ReportData(BaseModel):
    """Rows returned by execute_report_query (internal)."""

    row_count: int = Field(default=0, serialization_alias="rowCount")
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class RunReportResponse(BaseModel):
    title: str
    report_type: Optional[str] = Field(default=None, serialization_alias="reportType")
    columns: list[ReportColumnOut] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_count: int = Field(default=0, serialization_alias="totalCount")
    page: int = 1
    page_size: int = Field(default=25, serialization_alias="pageSize")
    filters: list[AvailableFilterOut] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: Optional[float] = Field(default=None, serialization_alias="durationMs")
    debug: Optional[dict[str, Any]] = None

    model_config = ConfigDict(populate_by_name=True)
