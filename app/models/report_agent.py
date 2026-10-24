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


class ReportCatalogItem(BaseModel):
    key: str
    title: str
    objective: str
    report_type: str = Field(..., serialization_alias="reportType")
    scope: str
    workflow_name: Optional[str] = Field(default=None, serialization_alias="workflowName")
    repository_name: Optional[str] = Field(default=None, serialization_alias="repositoryName")
    tag: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


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


class ReportSpecColumn(BaseModel):
    key: str
    label: str = ""
    formula: Optional[str] = None
    editable: bool = True

    model_config = ConfigDict(populate_by_name=True)


class ReportSpecFilter(BaseModel):
    field: str
    op: str = "eq"
    value: Optional[Any] = None
    editable: bool = True

    model_config = ConfigDict(populate_by_name=True)


class ReportSpecSort(BaseModel):
    field: str
    direction: str = "asc"

    model_config = ConfigDict(populate_by_name=True)


class ReportSpec(BaseModel):
    """Client-facing short editable report brief (Phase 1 UI)."""

    title: str = "Report"
    objective: str = ""
    report_type: Optional[str] = Field(
        default=None,
        serialization_alias="reportType",
        validation_alias=AliasChoices("reportType", "report_type"),
    )
    columns: list[ReportSpecColumn] = Field(default_factory=list)
    filters: list[ReportSpecFilter] = Field(default_factory=list)
    group_by: list[str] = Field(
        default_factory=list,
        serialization_alias="groupBy",
        validation_alias=AliasChoices("groupBy", "group_by"),
    )
    sort: Optional[ReportSpecSort] = None
    available_columns: list[str] = Field(
        default_factory=list,
        serialization_alias="availableColumns",
        validation_alias=AliasChoices("availableColumns", "available_columns"),
    )
    source_schema: Optional[str] = Field(
        default=None,
        serialization_alias="sourceSchema",
        validation_alias=AliasChoices("sourceSchema", "source_schema"),
    )
    source_table: Optional[str] = Field(
        default=None,
        serialization_alias="sourceTable",
        validation_alias=AliasChoices("sourceTable", "source_table"),
    )
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class GeneratePromptResponse(BaseModel):
    report_type: str = Field(..., serialization_alias="reportType")
    title: str
    report_prompt: str = Field(..., serialization_alias="reportPrompt")
    report_spec: Optional[ReportSpec] = Field(
        default=None,
        serialization_alias="reportSpec",
    )
    warnings: list[str] = Field(default_factory=list)
    schema_tables: list[str] = Field(default_factory=list, serialization_alias="schemaTables")
    duration_ms: Optional[float] = Field(default=None, serialization_alias="durationMs")

    model_config = ConfigDict(populate_by_name=True)


class ReportSortSpec(BaseModel):
    field: str
    direction: str = "asc"


class RunReportRequest(BaseModel):
    report_prompt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("reportPrompt", "report_prompt", "prompt"),
    )
    report_spec: Optional[ReportSpec] = Field(
        default=None,
        validation_alias=AliasChoices("reportSpec", "report_spec", "spec"),
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


class SaveReportRequest(BaseModel):
    name: str
    description: str
    tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("tenantId", "tenant_id", "TenantId"),
    )
    domain: Optional[str] = None
    report_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("reportType", "report_type"),
    )
    workflow_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workflowName", "workflow_name"),
    )
    repository_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("repositoryName", "repository_name"),
    )
    owner: Optional[str] = None
    visibility: str = "Private"
    tags: list[str] = Field(default_factory=list)
    status: str = "Draft"
    viz: str = "table"
    report_prompt: str = Field(default="", validation_alias=AliasChoices("reportPrompt", "report_prompt"))
    report_spec: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("reportSpec", "report_spec"),
    )

    model_config = ConfigDict(populate_by_name=True)


class UpdateReportRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    favorite: Optional[bool] = None
    report_prompt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("reportPrompt", "report_prompt"),
    )
    report_spec: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("reportSpec", "report_spec"),
    )

    model_config = ConfigDict(populate_by_name=True)


class SavedReportCard(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = ""
    owner: str = ""
    visibility: str = "Private"
    tags: list[str] = Field(default_factory=list)
    status: str = "Draft"
    favorite: bool = False
    views: int = 0
    runs: int = 0
    scheduled: bool = False
    version: int = 1
    viz: str = "table"
    created_at: Optional[str] = Field(
        default=None,
        serialization_alias="createdAt",
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    modified_at: Optional[str] = Field(
        default=None,
        serialization_alias="modifiedAt",
        validation_alias=AliasChoices("modifiedAt", "modified_at"),
    )
    last_generated_at: Optional[str] = Field(
        default=None,
        serialization_alias="lastGeneratedAt",
        validation_alias=AliasChoices("lastGeneratedAt", "last_generated_at"),
    )

    model_config = ConfigDict(populate_by_name=True)


class SavedReportDetail(SavedReportCard):
    report_type: Optional[str] = Field(
        default=None,
        serialization_alias="reportType",
        validation_alias=AliasChoices("reportType", "report_type"),
    )
    workflow_name: Optional[str] = Field(
        default=None,
        serialization_alias="workflowName",
        validation_alias=AliasChoices("workflowName", "workflow_name"),
    )
    repository_name: Optional[str] = Field(
        default=None,
        serialization_alias="repositoryName",
        validation_alias=AliasChoices("repositoryName", "repository_name"),
    )
    tenant_id: str = Field(
        default="",
        serialization_alias="tenantId",
        validation_alias=AliasChoices("tenantId", "tenant_id"),
    )
    report_prompt: str = Field(
        default="",
        serialization_alias="reportPrompt",
        validation_alias=AliasChoices("reportPrompt", "report_prompt"),
    )
    report_spec: dict[str, Any] = Field(
        default_factory=dict,
        serialization_alias="reportSpec",
        validation_alias=AliasChoices("reportSpec", "report_spec"),
    )
    schedule: Optional[Any] = None
