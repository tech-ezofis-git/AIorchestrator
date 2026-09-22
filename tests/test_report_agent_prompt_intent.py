"""Tests for Report Agent live prompt intent compilation, concept resolution, and dynamic plan updates."""
from __future__ import annotations

import pytest

from app.models.report_agent import GenerateReportPlanRequest
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.planner import create_report_plan
from app.report_agent.prompt_parser import compile_prompt_intent
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


def test_compile_prompt_intent_full_example():
    prompt = (
        "You are the Report Agent for EZOFIS.\n\n"
        "Create a report for:\n\n"
        "Title:\nAccounts Payable Aging\n\n"
        "Description:\nShow only unpaid invoices due in the next 30 days, grouped by vendor, highest amount first.\n\n"
        "Database schema discovered:\n"
        "Table: dbo.invoices\n"
        "Fields:\n"
        "- id (uuid)\n- invoice_number (text)\n- vendor_name (text)\n- total_amount (numeric)\n"
        "- due_date (date)\n- status (text)\n- created_at (timestamp with time zone)\n"
    )
    intent = compile_prompt_intent(prompt)
    assert intent.title == "Accounts Payable Aging"
    assert "unpaid" in (intent.description or "").lower()

    # Filters
    assert len(intent.requested_filters) >= 2
    status_filter = next((f for f in intent.requested_filters if f.concept == "status"), None)
    assert status_filter is not None
    assert "unpaid" in status_filter.value or "open" in status_filter.value

    date_filter = next((f for f in intent.requested_filters if f.concept == "due_date"), None)
    assert date_filter is not None
    assert date_filter.operator == "DUE_NEXT_DAYS"
    assert date_filter.value == 30

    # Grouping
    assert "vendor" in intent.group_by_concepts

    # Sorting
    assert len(intent.sort_concepts) > 0
    assert intent.sort_concepts[0].concept == "amount"
    assert intent.sort_concepts[0].direction == "DESC"


def test_compile_prompt_intent_relative_dates_and_sorting():
    p1 = "Show completed requests in the last 7 days, sorted by date desc"
    i1 = compile_prompt_intent(p1)
    d1 = next((f for f in i1.requested_filters if f.concept == "date_range"), None)
    assert d1 is not None
    assert d1.operator == "LAST_DAYS"
    assert d1.value == 7
    s1 = next((f for f in i1.requested_filters if f.concept == "status"), None)
    assert s1 is not None
    assert "completed" in s1.value
    assert len(i1.sort_concepts) > 0
    assert i1.sort_concepts[0].direction == "DESC"

    p2 = "Show items older than 90 days, lowest cost first"
    i2 = compile_prompt_intent(p2)
    d2 = next((f for f in i2.requested_filters if f.concept == "date_range"), None)
    assert d2 is not None
    assert d2.operator == "OLDER_THAN_DAYS"
    assert d2.value == 90
    assert len(i2.sort_concepts) > 0
    assert i2.sort_concepts[0].concept == "cost"
    assert i2.sort_concepts[0].direction == "ASC"


def test_create_report_plan_resolves_concepts_into_safe_plan():
    schema = _build_test_schema()
    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables = ["dbo.invoices"]
    from app.models.report_agent import DiscoveredField
    disc_fields = [
        DiscoveredField(table="dbo.invoices", column=c.column, type=c.data_type)
        for c in schema.get_columns_for_table("dbo", "invoices")
    ]

    prompt = (
        "Show only unpaid invoices due in the next 30 days, grouped by vendor, highest amount first"
    )
    intent = compile_prompt_intent(prompt)

    plan = create_report_plan(
        template=template,
        schema=schema,
        discovered_tables=disc_tables,
        discovered_fields=disc_fields,
        sampled_values={"distinct_values": {"status": ["unpaid", "paid", "draft"]}},
        prompt_intent=intent,
    )

    # 1. Group By resolved to vendor_name column
    assert "vendor_name" in plan.group_by

    # 2. Sort resolved to total_amount DESC
    assert any(s.field == "total_amount" and s.direction == "DESC" for s in plan.order_by)

    # 3. Filter resolved to status and due_date
    assert any(f.field == "due_date" and f.operator == "DUE_NEXT_DAYS" for f in plan.filters)
    assert any(f.field == "status" for f in plan.filters)

    # 4. SQL generation reflects the customized intent
    sql = generate_sql(plan)
    assert 'GROUP BY "vendor_name"' in sql
    assert 'ORDER BY "total_amount" DESC' in sql
    assert "DUE_NEXT_DAYS" not in sql  # must be expanded to PostgreSQL date expression
    assert 'CAST("due_date" AS DATE) >= CURRENT_DATE' in sql
    assert "INTERVAL '30 days'" in sql


def test_unmapped_concept_generates_warning_without_hallucinating():
    schema = _build_test_schema()
    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables = ["dbo.invoices"]
    from app.models.report_agent import DiscoveredField
    disc_fields = [
        DiscoveredField(table="dbo.invoices", column=c.column, type=c.data_type)
        for c in schema.get_columns_for_table("dbo", "invoices")
    ]

    # Prompt requests grouping by a nonexistent concept ("department")
    prompt = "Show invoices grouped by department, sorted by priority"
    intent = compile_prompt_intent(prompt)

    plan = create_report_plan(
        template=template,
        schema=schema,
        discovered_tables=disc_tables,
        discovered_fields=disc_fields,
        sampled_values={},
        prompt_intent=intent,
    )

    # Must NOT hallucinate "department" or "priority" column in SQL
    assert "department" not in plan.group_by
    assert not any(s.field == "priority" for s in plan.order_by)

    # Must contain clear user-friendly warnings
    assert any("department" in w for w in plan.warnings)
    assert any("priority" in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_service_live_prompt_refresh_pipeline():
    schema = _build_test_schema()

    class MockDb:
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
                    for c in schema.all_columns
                ]
            return [
                {
                    "id": "1",
                    "invoice_number": "INV-101",
                    "vendor_name": "Acme Corp",
                    "total_amount": "1500.00",
                    "due_date": "2026-10-10",
                    "status": "unpaid",
                    "created_at": "2026-09-01",
                }
            ]

    service = ReportAgentService(db_pool=MockDb())

    # 1. Default request
    res_default = await service.generate_report_plan(
        GenerateReportPlanRequest(template_id="tpl-accounts-payable-aging")
    )
    assert res_default.report_plan.group_by == []

    # 2. Edited prompt request
    edited_prompt = (
        "Show only unpaid invoices due in the next 30 days, grouped by vendor, highest amount first"
    )
    res_edited = await service.generate_report_plan(
        GenerateReportPlanRequest(
            template_id="tpl-accounts-payable-aging",
            prompt=edited_prompt,
        )
    )

    assert "vendor_name" in res_edited.report_plan.group_by
    assert res_edited.report_plan.order_by[0].field == "total_amount"
    assert res_edited.report_plan.order_by[0].direction == "DESC"
    assert 'GROUP BY "vendor_name"' in res_edited.data_query.sql
    assert res_edited.validation.valid is True
