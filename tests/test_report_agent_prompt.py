"""Tests for Report Agent Dynamic Template Prompt Generation (Phase 1)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.report_agent import GeneratePromptRequest
from app.report_agent.field_discovery import find_relevant_fields
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema, get_database_schema
from app.report_agent.prompt_generator import generate_dynamic_prompt
from app.report_agent.service import ReportAgentService
from app.report_agent.templates import SUPPORTED_TEMPLATES, get_template, list_templates


class MockDbWithSchema:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    async def fetch(self, query: str, *args):
        return self.rows


@pytest.fixture
def mock_enterprise_schema_rows() -> list[dict]:
    """Realistic PostgreSQL information_schema rows for an enterprise tenant."""
    return [
        # AP / Invoices
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "invoice_number", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "vendor_name", "data_type": "text", "udt_name": "text", "is_nullable": "YES"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "total_amount", "data_type": "numeric", "udt_name": "numeric", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "due_date", "data_type": "date", "udt_name": "date", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "status", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices", "column_name": "created_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
        # Workflows
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "workflow_name", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "request_no", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "current_status", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "current_step", "data_type": "text", "udt_name": "text", "is_nullable": "YES"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "created_by", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "created_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "finished_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "YES"},
        {"table_schema": "workflow", "table_name": "workflow_instances_main", "column_name": "sla_hours", "data_type": "integer", "udt_name": "int4", "is_nullable": "YES"},
        # Documents / Repositories
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "ifilename", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "repository_name", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "version_number", "data_type": "integer", "udt_name": "int4", "is_nullable": "NO"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "last_access_date", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "YES"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "retention_status", "data_type": "text", "udt_name": "text", "is_nullable": "YES"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "deletion_date", "data_type": "date", "udt_name": "date", "is_nullable": "YES"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "modified_by", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "YES"},
        {"table_schema": "repository", "table_name": "repositoryitem", "column_name": "modified_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
        # Portal / Form Submissions
        {"table_schema": "dbo", "table_name": "ezfb_submissions_items", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ezfb_submissions_items", "column_name": "form_name", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ezfb_submissions_items", "column_name": "submitted_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ezfb_submissions_items", "column_name": "status", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        # Security & Audit
        {"table_schema": "dbo", "table_name": "audit_log", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "audit_log", "column_name": "user_id", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "audit_log", "column_name": "client_ip", "data_type": "text", "udt_name": "text", "is_nullable": "YES"},
        {"table_schema": "dbo", "table_name": "audit_log", "column_name": "status", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "audit_log", "column_name": "created_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
        # AI Credits & ROI
        {"table_schema": "dbo", "table_name": "ap_credit_ledger", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ap_credit_ledger", "column_name": "tenant_id", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ap_credit_ledger", "column_name": "skill_id", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ap_credit_ledger", "column_name": "credits_charged", "data_type": "integer", "udt_name": "int4", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "ap_credit_ledger", "column_name": "created_at", "data_type": "timestamp with time zone", "udt_name": "timestamptz", "is_nullable": "NO"},
    ]


def test_list_templates_returns_active_templates():
    templates = list_templates()
    assert len(templates) == 2
    ids = {t.id for t in templates}
    expected_ids = {
        "tpl-accounts-payable-aging",
        "tpl-pending-workflow-requests",
    }
    assert ids == expected_ids


@pytest.mark.asyncio
async def test_get_database_schema_inspects_columns(mock_enterprise_schema_rows):
    db = MockDbWithSchema(mock_enterprise_schema_rows)
    schema = await get_database_schema(db)
    assert len(schema.tables) >= 6
    assert len(schema.all_columns) == len(mock_enterprise_schema_rows)
    # Check table columns lookup
    inv_cols = schema.get_columns_for_table("dbo", "invoices")
    assert any(c.column == "invoice_number" for c in inv_cols)


@pytest.mark.asyncio
async def test_all_10_templates_field_discovery_and_prompt(mock_enterprise_schema_rows):
    db = MockDbWithSchema(mock_enterprise_schema_rows)
    schema = await get_database_schema(db)

    for template_id, template_def in SUPPORTED_TEMPLATES.items():
        discovered_tables, discovered_fields, missing_fields = find_relevant_fields(template_def, schema)
        assert len(discovered_tables) > 0, f"Template {template_id} failed to discover any tables"
        assert len(discovered_fields) > 0, f"Template {template_id} failed to discover any fields"

        # Generate prompt
        prompt = generate_dynamic_prompt(template_def, discovered_tables, discovered_fields, missing_fields)
        assert template_def.title in prompt
        assert template_def.domain in prompt
        assert "Database schema discovered:" in prompt
        assert "Instructions:" in prompt
        assert "Use ONLY fields that actually exist in the discovered schema." in prompt

        # Verify discovered fields appear in prompt with data types
        for field in discovered_fields:
            assert field.column in prompt
            assert field.type in prompt


@pytest.mark.asyncio
async def test_missing_fields_identified_and_included_in_prompt():
    # Schema with table but missing date columns for aging/timeframe report
    sparse_rows = [
        {"table_schema": "dbo", "table_name": "invoices_sparse", "column_name": "id", "data_type": "uuid", "udt_name": "uuid", "is_nullable": "NO"},
        {"table_schema": "dbo", "table_name": "invoices_sparse", "column_name": "status", "data_type": "text", "udt_name": "text", "is_nullable": "NO"},
    ]
    db = MockDbWithSchema(sparse_rows)
    schema = await get_database_schema(db)

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    discovered_tables, discovered_fields, missing_fields = find_relevant_fields(template, schema)
    assert len(missing_fields) > 0
    prompt = generate_dynamic_prompt(template, discovered_tables, discovered_fields, missing_fields)

    assert "Missing Field Warnings:" in prompt
    assert any("discovered database schema" in mf.reason for mf in missing_fields)


@pytest.mark.asyncio
async def test_service_unsupported_template_raises():
    service = ReportAgentService(db_pool=MockDbWithSchema([]))
    with pytest.raises(ValueError, match="Unsupported report template"):
        await service.generate_prompt(GeneratePromptRequest(template_id="tpl-non-existent-report"))


@pytest.mark.asyncio
async def test_service_db_unavailable_raises():
    service = ReportAgentService(db_pool=None)
    with pytest.raises(RuntimeError, match="Unable to access database metadata"):
        await service.generate_prompt(GeneratePromptRequest(template_id="tpl-pending-workflow-requests"))


def test_api_list_templates_endpoint(client):
    response = client.get("/api/report-agent/templates")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(t["id"] == "tpl-pending-workflow-requests" for t in data)


def test_api_generate_prompt_endpoint_unsupported_template(client):
    response = client.post(
        "/api/report-agent/generate-prompt",
        json={"templateId": "tpl-unknown-id"},
    )
    assert response.status_code == 400
    assert "Unsupported report template" in response.json()["detail"]


def test_api_generate_prompt_endpoint_success(client, mock_enterprise_schema_rows, monkeypatch):
    mock_db = MockDbWithSchema(mock_enterprise_schema_rows)
    client.app.state.report_agent_service._db_pool = mock_db

    response = client.post(
        "/api/report-agent/generate-prompt",
        json={"templateId": "tpl-pending-workflow-requests"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["templateId"] == "tpl-pending-workflow-requests"
    assert body["title"] == "Pending Workflow Requests"
    assert len(body["discoveredTables"]) > 0
    assert len(body["discoveredFields"]) > 0
    assert "prompt" in body
    assert "workflow_instances_main" in body["prompt"]
    assert "workflow_name" in body["prompt"]
