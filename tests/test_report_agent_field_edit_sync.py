"""Tests for Report Agent live prompt field editing, validation, and Report Plan synchronization."""
from __future__ import annotations

import pytest

from app.models.report_agent import GenerateReportPlanRequest
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.prompt_parser import compile_prompt_intent, parse_prompt_metadata
from app.report_agent.service import ReportAgentService
from app.report_agent.sql_generator import generate_sql
from app.report_agent.templates import get_template


def _build_test_schema() -> DatabaseSchema:
    schema = DatabaseSchema()
    cols = [
        ColumnMeta("dbo", "invoices", "id", "uuid"),
        ColumnMeta("dbo", "invoices", "invoice_number", "text"),
        ColumnMeta("dbo", "invoices", "vendor_name", "text"),
        ColumnMeta("dbo", "invoices", "total_amount", "numeric"),
        ColumnMeta("dbo", "invoices", "due_date", "date"),
        ColumnMeta("dbo", "invoices", "status", "text"),
        ColumnMeta("dbo", "invoices", "created_at", "timestamp with time zone"),
    ]
    for c in cols:
        schema.all_columns.append(c)
        key = (c.schema, c.table)
        schema.columns_by_table.setdefault(key, []).append(c)
        if key not in schema.tables:
            schema.tables.append(key)
    return schema


class MockDb:
    def __init__(self, schema: DatabaseSchema):
        self._schema = schema

    async def fetch(self, query: str, *args):
        q_lower = query.lower()
        if "information_schema.columns" in q_lower:
            return [
                {
                    "table_schema": c.schema,
                    "table_name": c.table,
                    "column_name": c.column,
                    "data_type": c.data_type,
                    "udt_name": c.data_type,
                    "is_nullable": "YES",
                }
                for c in self._schema.all_columns
            ]
        # Return mock row with values for all columns
        return [
            {
                "id": "11111111-2222-3333-4444-555555555555",
                "invoice_number": "INV-2026-001",
                "vendor_name": "Acme Supplies",
                "total_amount": 1250.50,
                "due_date": "2026-10-15",
                "status": "unpaid",
                "created_at": "2026-09-01 10:00:00",
            }
        ]


@pytest.mark.asyncio
async def test_prompt_field_deletion_reflects_in_plan_and_sql():
    """When a user deletes fields from the prompt, only remaining fields are in plan and SQL."""
    schema = _build_test_schema()
    service = ReportAgentService(db_pool=MockDb(schema))

    # User edited the prompt to ONLY keep invoice_number, vendor_name, and total_amount
    prompt_with_reduced_fields = (
        "You are the Report Agent for EZOFIS.\n\n"
        "Create a report for:\n\n"
        "Title:\nAccounts Payable Aging\n\n"
        "Description:\nOutstanding AP requests bucketed by age.\n\n"
        "Domain:\nAccounts Payable\n\n"
        "Database schema discovered:\n\n"
        "Table: dbo.invoices\n"
        "Fields:\n"
        "- invoice_number (text)\n"
        "- vendor_name (text)\n"
        "- total_amount (numeric)\n\n"
        "Instructions:\n"
        "Use only the fields above."
    )

    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=prompt_with_reduced_fields,
    )
    res = await service.generate_report_plan(req)

    # Verify report plan columns contain ONLY the 3 edited fields
    col_fields = [c.field for c in res.report_plan.columns]
    assert col_fields == ["invoice_number", "vendor_name", "total_amount"]

    # Verify generated SQL selects only these columns
    assert 'SELECT\n    "invoice_number",\n    "vendor_name",\n    "total_amount"' in res.data_query.sql
    assert '"due_date"' not in res.data_query.sql
    assert '"created_at"' not in res.data_query.sql
    assert res.validation.checks["sql_safety"] is True
    assert res.validation.checks["columns_present"] is True
    # When user deliberately removed date field, domain advisory warning is correctly flagged
    assert any("essential Accounts Payable fields" in w for w in res.validation.warnings)


@pytest.mark.asyncio
async def test_prompt_field_addition_reflects_in_plan_and_sql():
    """When a user adds a valid schema column to the prompt, it is included in plan and SQL."""
    schema = _build_test_schema()
    service = ReportAgentService(db_pool=MockDb(schema))

    prompt_with_added_fields = (
        "Create a report for:\n\n"
        "Title: Accounts Payable Aging\n\n"
        "Table: dbo.invoices\n"
        "Fields:\n"
        "- id (uuid)\n"
        "- invoice_number (text)\n"
        "- status (text)\n"
        "- due_date (date)\n"
    )

    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=prompt_with_added_fields,
    )
    res = await service.generate_report_plan(req)

    col_fields = [c.field for c in res.report_plan.columns]
    assert col_fields == ["id", "invoice_number", "status", "due_date"]
    assert '"invoice_number"' in res.data_query.sql
    assert '"status"' in res.data_query.sql


@pytest.mark.asyncio
async def test_prompt_invalid_field_omitted_with_warning():
    """When a user specifies a non-existent column, it is omitted from SQL and reported as warning."""
    schema = _build_test_schema()
    service = ReportAgentService(db_pool=MockDb(schema))

    prompt_with_hallucinated_field = (
        "Create a report for:\n\n"
        "Title: Accounts Payable Aging\n\n"
        "Table: dbo.invoices\n"
        "Fields:\n"
        "- invoice_number (text)\n"
        "- fake_column_not_in_db (text)\n"
        "- total_amount (numeric)\n"
    )

    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=prompt_with_hallucinated_field,
    )
    res = await service.generate_report_plan(req)

    col_fields = [c.field for c in res.report_plan.columns]
    assert "fake_column_not_in_db" not in col_fields
    assert col_fields == ["invoice_number", "total_amount"]
    assert "fake_column_not_in_db" not in res.data_query.sql

    # Warning present in validation / plan warnings
    assert any("fake_column_not_in_db" in w for w in res.validation.warnings)


@pytest.mark.asyncio
async def test_prompt_inline_column_specification():
    """When a user specifies columns inline e.g. 'Columns: id, vendor_name, total_amount'."""
    schema = _build_test_schema()
    service = ReportAgentService(db_pool=MockDb(schema))

    prompt_with_inline_cols = (
        "Create a report for Accounts Payable Aging.\n\n"
        "Columns: id, vendor_name, total_amount\n"
        "Show only unpaid invoices."
    )

    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=prompt_with_inline_cols,
    )
    res = await service.generate_report_plan(req)

    col_fields = [c.field for c in res.report_plan.columns]
    assert col_fields == ["id", "vendor_name", "total_amount"]
    assert 'SELECT\n    "id",\n    "vendor_name",\n    "total_amount"' in res.data_query.sql


@pytest.mark.asyncio
async def test_prompt_numeric_filter_and_grouping():
    """When a user edits filters with numeric comparisons like 'total_amount > 1000' and 'group by vendor_name'."""
    schema = _build_test_schema()
    service = ReportAgentService(db_pool=MockDb(schema))

    prompt = (
        "Title: Accounts Payable Aging\n\n"
        "Description: Show only unpaid invoices with total_amount > 1000, grouped by vendor_name, highest total_amount first\n\n"
        "Table: dbo.invoices\n"
        "Fields:\n"
        "- vendor_name (text)\n"
        "- total_amount (numeric)\n"
    )

    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=prompt,
    )
    res = await service.generate_report_plan(req)

    assert "vendor_name" in res.report_plan.group_by
    assert any(f.field == "total_amount" and f.operator == ">" and f.value == 1000 for f in res.report_plan.filters)
    assert 'GROUP BY "vendor_name"' in res.data_query.sql
    assert '"total_amount" > 1000' in res.data_query.sql
    assert 'ORDER BY "total_amount" DESC' in res.data_query.sql


@pytest.mark.asyncio
async def test_pasting_prompt_for_different_template_resolves_context():
    """When a user pastes a prompt generated for another template, the agent resolves to that template."""
    schema = DatabaseSchema()
    cols = [
        ColumnMeta("dbo", "workflow_requests", "request_id", "uuid"),
        ColumnMeta("dbo", "workflow_requests", "workflow_name", "text"),
        ColumnMeta("dbo", "workflow_requests", "current_status", "text"),
        ColumnMeta("dbo", "workflow_requests", "current_step", "text"),
        ColumnMeta("dbo", "workflow_requests", "created_at", "timestamp with time zone"),
    ]
    for c in cols:
        schema.all_columns.append(c)
        key = (c.schema, c.table)
        schema.columns_by_table.setdefault(key, []).append(c)
        if key not in schema.tables:
            schema.tables.append(key)

    service = ReportAgentService(db_pool=MockDb(schema))

    pasted_prompt = (
        "You are the Report Agent for EZOFIS.\n\n"
        "Create a report for:\n\n"
        "Title:\nPending Workflow Requests\n\n"
        "Description:\nOpen workflow requests awaiting action.\n\n"
        "Domain:\nWorkflow Automation\n\n"
        "Database schema discovered:\n\n"
        "Table: dbo.workflow_requests\n"
        "Fields:\n"
        "- request_id (uuid)\n"
        "- workflow_name (text)\n"
        "- current_status (text)\n"
    )

    # Request has default templateId from UI dropdown, but prompt contains "Pending Workflow Requests"
    req = GenerateReportPlanRequest(
        template_id="tpl-accounts-payable-aging",
        prompt=pasted_prompt,
    )
    res = await service.generate_report_plan(req)

    assert res.template_id == "tpl-pending-workflow-requests"
    assert res.report_plan.title == "Pending Workflow Requests"
    assert res.report_plan.source.table == "workflow_requests"
    col_fields = [c.field for c in res.report_plan.columns]
    assert col_fields == ["request_id", "workflow_name", "current_status"]
