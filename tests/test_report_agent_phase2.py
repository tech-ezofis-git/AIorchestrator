"""Automated tests for Phase 2 of the Report Agent (Report Plan & Live DB Data Execution)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.report_agent import (
    DiscoveredField,
    DiscoveredSchemaSummary,
    GenerateReportPlanRequest,
    ReportCalculation,
    ReportColumn,
    ReportData,
    ReportFilter,
    ReportPlan,
    ReportSort,
    ReportSource,
)
from app.report_agent.data_service import execute_report_query, sample_table_values
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.planner import create_report_plan
from app.report_agent.report_validator import validate_report_data
from app.report_agent.service import ReportAgentService
from app.report_agent.sql_generator import generate_sql
from app.report_agent.sql_validator import validate_read_only_sql
from app.report_agent.templates import SUPPORTED_TEMPLATES, get_template


class MockRecord(dict):
    """Mock asyncpg Record object that supports both dict and attr/key indexing."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def keys(self):
        return list(super().keys())


class MockDbPool:
    """Mock database pool returning simulated metadata and report data."""

    def __init__(self):
        self.last_query = ""

    async def fetch(self, query: str, *args):
        self.last_query = query
        q_lower = query.lower()

        # 1. Metadata introspection query
        if "from information_schema.columns" in q_lower:
            return [
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="instance_id",
                    data_type="uuid",
                    udt_name="uuid",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="workflow_name",
                    data_type="text",
                    udt_name="text",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="current_status",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="current_step",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="created_at",
                    data_type="timestamp without time zone",
                    udt_name="timestamp",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="workflow",
                    table_name="workflow_instances",
                    column_name="completed_at",
                    data_type="timestamp without time zone",
                    udt_name="timestamp",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="invoice_id",
                    data_type="uuid",
                    udt_name="uuid",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="vendor_name",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="invoice_number",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="total_amount",
                    data_type="numeric",
                    udt_name="numeric",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="due_date",
                    data_type="date",
                    udt_name="date",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ezfb_invoices",
                    column_name="payment_status",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="item_id",
                    data_type="uuid",
                    udt_name="uuid",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="ifilename",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="repository_name",
                    data_type="text",
                    udt_name="text",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="created_at",
                    data_type="timestamp without time zone",
                    udt_name="timestamp",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="last_access_date",
                    data_type="timestamp without time zone",
                    udt_name="timestamp",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="dbo",
                    table_name="repositoryitem",
                    column_name="is_deleted",
                    data_type="boolean",
                    udt_name="bool",
                    is_nullable="YES",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ap_credit_ledger",
                    column_name="run_id",
                    data_type="uuid",
                    udt_name="uuid",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ap_credit_ledger",
                    column_name="tenant_id",
                    data_type="text",
                    udt_name="text",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ap_credit_ledger",
                    column_name="skill_id",
                    data_type="text",
                    udt_name="text",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ap_credit_ledger",
                    column_name="credits_charged",
                    data_type="numeric",
                    udt_name="numeric",
                    is_nullable="NO",
                ),
                MockRecord(
                    table_schema="public",
                    table_name="ap_credit_ledger",
                    column_name="created_at",
                    data_type="timestamp without time zone",
                    udt_name="timestamp",
                    is_nullable="NO",
                ),
            ]

        # 2. Distinct status query for sampling
        if "select distinct" in q_lower:
            if "status" in q_lower:
                return [
                    MockRecord(val="Pending"),
                    MockRecord(val="In Progress"),
                    MockRecord(val="Completed"),
                ]
            return [MockRecord(val="Standard")]

        # 3. Sample rows query
        if "from \"workflow\".\"workflow_instances\"" in q_lower or "workflow_instances" in q_lower:
            return [
                MockRecord(
                    instance_id=uuid.uuid4(),
                    workflow_name="Purchase Requisition",
                    current_status="Pending",
                    current_step="Manager Approval",
                    created_at=datetime(2026, 3, 1, 10, 0, 0),
                    completed_at=None,
                ),
                MockRecord(
                    instance_id=uuid.uuid4(),
                    workflow_name="Invoice Processing",
                    current_status="In Progress",
                    current_step="Finance Review",
                    created_at=datetime(2026, 3, 2, 11, 30, 0),
                    completed_at=None,
                ),
            ]

        if "ezfb_invoices" in q_lower:
            return [
                MockRecord(
                    invoice_id=uuid.uuid4(),
                    vendor_name="Acme Corp",
                    invoice_number="INV-2026-001",
                    total_amount=Decimal("12500.50"),
                    due_date=date(2026, 2, 15),
                    payment_status="Pending",
                ),
                MockRecord(
                    invoice_id=uuid.uuid4(),
                    vendor_name="Globex Tech",
                    invoice_number="INV-2026-002",
                    total_amount=Decimal("8400.00"),
                    due_date=date(2026, 1, 10),
                    payment_status="Pending",
                ),
            ]

        # Default dummy rows for any other table
        return [
            MockRecord(
                item_id=uuid.uuid4(),
                ifilename="Financial_Report_Q1.pdf",
                repository_name="Finance Docs",
                created_at=datetime(2026, 1, 15, 9, 0, 0),
                last_access_date=datetime(2026, 1, 20, 14, 0, 0),
                is_deleted=False,
            )
        ]


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------


def test_sql_validator_allows_safe_queries():
    """SQL validator must accept valid SELECT and WITH queries."""
    safe_queries = [
        'SELECT "request_id", "status" FROM "workflow_requests" LIMIT 50;',
        'WITH recent AS (SELECT * FROM invoices) SELECT * FROM recent WHERE amount > 100 LIMIT 20;',
        'SELECT "status", COUNT(*) FROM "workflow_requests" GROUP BY "status" ORDER BY COUNT(*) DESC LIMIT 50;',
        "SELECT *, CASE WHEN due_date < CURRENT_DATE THEN 'Overdue' ELSE 'Current' END AS bucket FROM ap_records LIMIT 50;",
    ]
    for sql in safe_queries:
        valid, errors = validate_read_only_sql(sql)
        assert valid is True, f"Expected safe SQL to pass, but got errors: {errors}"


def test_sql_validator_rejects_mutations():
    """SQL validator must strictly reject DML/DDL mutation statements."""
    dangerous_queries = [
        "DELETE FROM workflow_requests WHERE id = 1;",
        "DROP TABLE repositoryitem;",
        "UPDATE invoices SET status = 'Approved';",
        "INSERT INTO audit_log (msg) VALUES ('hacked');",
        "TRUNCATE TABLE users;",
        "ALTER TABLE users ADD COLUMN password text;",
        "SELECT * FROM users; DROP TABLE users;",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity;",
        "SELECT lo_import('/etc/passwd');",
    ]
    for sql in dangerous_queries:
        valid, errors = validate_read_only_sql(sql)
        assert valid is False, f"Expected mutation SQL '{sql}' to be rejected, but it passed!"
        assert len(errors) > 0


def test_sql_generator():
    """SQL generator should build correct SELECT queries from ReportPlan."""
    plan = ReportPlan(
        title="Pending Workflow Requests",
        description="Open workflow requests awaiting action.",
        template_id="tpl-pending-workflow-requests",
        source=ReportSource(table="workflow_instances", schema_name="workflow"),
        columns=[
            ReportColumn(field="instance_id", label="Instance ID", type="uuid"),
            ReportColumn(field="workflow_name", label="Workflow Name", type="text"),
            ReportColumn(field="current_status", label="Status", type="text"),
        ],
        filters=[
            ReportFilter(field="current_status", operator="IN", value=["Pending", "In Progress"]),
        ],
        calculations=[
            ReportCalculation(label="Is Open", expression="current_status != 'Completed'", type="boolean")
        ],
        order_by=[ReportSort(field="created_at", direction="DESC")],
    )

    sql = generate_sql(plan, limit=50)
    assert 'SELECT' in sql
    assert '"instance_id"' in sql
    assert '"workflow_name"' in sql
    assert '"workflow"."workflow_instances"' in sql
    assert "WHERE \"current_status\" IN ('Pending', 'In Progress')" in sql
    assert 'ORDER BY "created_at" DESC' in sql
    assert 'LIMIT 50;' in sql


def test_report_planner_for_all_10_templates():
    """Report planner must build valid plans for all 10 supported templates."""
    schema = DatabaseSchema(
        tables=[("workflow", "workflow_instances"), ("public", "ezfb_invoices")],
        all_columns=[
            ColumnMeta(schema="workflow", table="workflow_instances", column="instance_id", data_type="uuid"),
            ColumnMeta(schema="workflow", table="workflow_instances", column="workflow_name", data_type="text"),
            ColumnMeta(schema="workflow", table="workflow_instances", column="current_status", data_type="text"),
            ColumnMeta(schema="workflow", table="workflow_instances", column="created_at", data_type="timestamp"),
        ],
    )
    disc_fields = [
        DiscoveredField(table="workflow.workflow_instances", column="instance_id", type="uuid"),
        DiscoveredField(table="workflow.workflow_instances", column="workflow_name", type="text"),
        DiscoveredField(table="workflow.workflow_instances", column="current_status", type="text"),
        DiscoveredField(table="workflow.workflow_instances", column="created_at", type="timestamp"),
    ]

    for t_id, t_def in SUPPORTED_TEMPLATES.items():
        plan = create_report_plan(
            template=t_def,
            schema=schema,
            discovered_tables=["workflow.workflow_instances"],
            discovered_fields=disc_fields,
            sampled_values={"distinct_values": {"current_status": ["Pending", "Completed"]}},
        )
        assert plan.template_id == t_id
        assert plan.title == t_def.title
        assert len(plan.columns) > 0
        assert plan.source.table is not None


def test_report_validator():
    """Report validator must verify returned data matches planned columns and types."""
    plan = ReportPlan(
        title="Pending Workflow Requests",
        description="Open workflow requests awaiting action.",
        template_id="tpl-pending-workflow-requests",
        source=ReportSource(table="workflow_instances"),
        columns=[
            ReportColumn(field="instance_id", label="Instance ID", type="uuid"),
            ReportColumn(field="workflow_name", label="Workflow Name", type="text"),
        ],
    )
    data = ReportData(
        row_count=1,
        columns=["instance_id", "workflow_name"],
        rows=[{"instance_id": str(uuid.uuid4()), "workflow_name": "PO Approval"}],
    )

    validation = validate_report_data(plan, data)
    assert validation.valid is True
    assert validation.checks["columns_present"] is True
    assert validation.checks["types_compatible"] is True


# ---------------------------------------------------------------------------
# Service & API Endpoint Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_generate_report_plan():
    """ReportAgentService.generate_report_plan returns complete Phase 2 structured output."""
    mock_db = MockDbPool()
    service = ReportAgentService(db_pool=mock_db)

    req = GenerateReportPlanRequest(
        template_id="tpl-pending-workflow-requests",
        limit=25,
    )
    resp = await service.generate_report_plan(req)

    assert resp.template_id == "tpl-pending-workflow-requests"
    assert resp.report_plan.title == "Pending Workflow Requests"
    assert len(resp.report_plan.columns) > 0
    assert resp.data_query.read_only is True
    assert "SELECT" in resp.data_query.sql
    assert resp.data.row_count > 0
    assert resp.validation.valid is True
    assert resp.validation.checks["sql_safety"] is True


def test_api_generate_report_plan_endpoint(client):
    """FastAPI endpoint POST /api/report-agent/generate-report-plan should return 200 with full plan."""
    mock_db = MockDbPool()
    client.app.state.report_agent_service._db_pool = mock_db

    # 1. Test POST /api/report-agent/generate-report-plan for Accounts Payable Aging
    res = client.post(
        "/api/report-agent/generate-report-plan",
        json={"templateId": "tpl-accounts-payable-aging", "limit": 20},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["templateId"] == "tpl-accounts-payable-aging"
    assert "reportPlan" in data
    assert "databaseSchema" in data
    assert "dataQuery" in data
    assert "data" in data
    assert "validation" in data
    assert data["dataQuery"]["readOnly"] is True

    # 2. Test unsupported template ID returns 400
    bad_res = client.post(
        "/api/report-agent/generate-report-plan",
        json={"templateId": "tpl-non-existent-template"},
    )
    assert bad_res.status_code == 400


@pytest.mark.asyncio
async def test_all_10_templates_generate_report_plan():
    """All 10 supported report templates must successfully generate Report Plans & Data."""
    mock_db = MockDbPool()
    service = ReportAgentService(db_pool=mock_db)

    for template_id in SUPPORTED_TEMPLATES.keys():
        req = GenerateReportPlanRequest(template_id=template_id, limit=10)
        resp = await service.generate_report_plan(req)
        assert resp.template_id == template_id
        assert resp.report_plan is not None
        assert resp.data_query.read_only is True
        assert resp.validation.valid is True


def test_generate_report_plan_from_raw_prompt(client):
    """Passing a raw Phase 1 prompt without explicit templateId should parse and generate a valid Report Plan."""
    raw_prompt = """You are the Report Agent for EZOFIS, an enterprise document and workflow platform.

Create a report for:

Title:
Portal Submission Performance

Description:
Submission volume and completion rate trends.

Domain:
External Portal

Database schema discovered:

Table: ezfb_d7ad8ffb_items
Fields:
- item_id (uuid)
- Enquiry_Form (text)
- Customer (text)
- Shipper (text)
- Vessel (text)
- created_at (timestamp without time zone)
- is_deleted (boolean)

Report Requirements:
1. Analyze external portal submission volumes, processing turnaround, and completion rates over time.
"""
    mock_db = MockDbPool()
    client.app.state.report_agent_service._db_pool = mock_db

    # Test passing only prompt in JSON payload
    res = client.post(
        "/api/report-agent/generate-report-plan",
        json={"prompt": raw_prompt},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["templateId"] == "tpl-portal-submission-performance"
    assert data["reportPlan"]["title"] == "Portal Submission Performance"
    # Prompt-provided schema is informational only; the source must come from the live mock schema.
    assert data["reportPlan"]["source"]["table"] in ("ezfb_invoices", "repositoryitem", "workflow_instances")
    assert len(data["reportPlan"]["columns"]) > 0
    assert data["dataQuery"]["readOnly"] is True
    assert data["validation"]["valid"] is True


@pytest.mark.asyncio
async def test_report_plan_uses_live_schema_not_prompt_supplied_fields():
    """Prompt schema text must not inject a table, column, or data type into a plan."""
    service = ReportAgentService(db_pool=MockDbPool())
    prompt = """Title:
Pending Workflow Requests

Database schema discovered:

Table: fabricated_table
Fields:
- fabricated_status (numeric)
- fabricated_secret (text)
"""

    response = await service.generate_report_plan(GenerateReportPlanRequest(prompt=prompt))

    plan_fields = [column.field for column in response.report_plan.columns]
    discovered_fields = [field.column for field in response.database_schema.fields]
    assert response.report_plan.source.table != "fabricated_table"
    assert "fabricated_status" not in plan_fields
    assert "fabricated_secret" not in plan_fields
    assert "fabricated_status" not in discovered_fields
    assert "fabricated_secret" not in discovered_fields
    assert "fabricated_table" not in response.data_query.sql
