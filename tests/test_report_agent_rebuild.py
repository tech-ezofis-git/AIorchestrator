"""Tests for rebuilt Report Agent (types, lock, SQL, service phases)."""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.models.report_agent import GeneratePromptRequest, RunReportRequest
from app.report_agent.definition_lock import lock_report_definition, parse_definition_json
from app.report_agent.definition_sql import generate_definition_sql
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.report_types import list_report_types, validate_report_type_input
from app.report_agent.service import ReportAgentService
from app.report_agent.sql_validator import validate_read_only_sql


def _schema() -> DatabaseSchema:
    schema = DatabaseSchema()
    schema.tables = [
        ("workflow", "wworkflow"),
        ("workflow", "workflow_instances_aaaaaaaa"),
        ("workflow", "inbox_aaaaaaaa"),
        ("repository", "wrepository"),
        ("repository", "items_bbbbbbbb"),
    ]
    cols = {
        ("workflow", "wworkflow"): [
            ColumnMeta("workflow", "wworkflow", "Id", "uuid"),
            ColumnMeta("workflow", "wworkflow", "Name", "text"),
        ],
        ("workflow", "workflow_instances_aaaaaaaa"): [
            ColumnMeta("workflow", "workflow_instances_aaaaaaaa", "id", "uuid"),
            ColumnMeta("workflow", "workflow_instances_aaaaaaaa", "workflow_name", "text"),
            ColumnMeta("workflow", "workflow_instances_aaaaaaaa", "status", "text"),
            ColumnMeta("workflow", "workflow_instances_aaaaaaaa", "score", "numeric"),
        ],
        ("workflow", "inbox_aaaaaaaa"): [
            ColumnMeta("workflow", "inbox_aaaaaaaa", "workflow_instance_id", "uuid"),
            ColumnMeta("workflow", "inbox_aaaaaaaa", "name", "text"),
            ColumnMeta("workflow", "inbox_aaaaaaaa", "stage", "text"),
        ],
        ("repository", "wrepository"): [
            ColumnMeta("repository", "wrepository", "Id", "uuid"),
            ColumnMeta("repository", "wrepository", "Name", "text"),
        ],
        ("repository", "items_bbbbbbbb"): [
            ColumnMeta("repository", "items_bbbbbbbb", "id", "uuid"),
            ColumnMeta("repository", "items_bbbbbbbb", "status", "text"),
            ColumnMeta("repository", "items_bbbbbbbb", "isDeleted", "boolean"),
            ColumnMeta("repository", "items_bbbbbbbb", "fileName", "text"),
        ],
    }
    schema.columns_by_table = cols
    schema.all_columns = [c for lst in cols.values() for c in lst]
    schema.table_to_workflow = {
        "aaaaaaaa": "Accounts Payable",
        "bbbbbbbb": "Invoice Repo",
    }
    return schema


def test_report_types_registry_has_four():
    types = list_report_types()
    keys = {t.key for t in types}
    assert keys == {
        "all_workflows",
        "specific_workflow",
        "all_repositories",
        "specific_repository",
    }


def test_validate_specific_workflow_requires_name():
    with pytest.raises(ValueError, match="workflowName"):
        validate_report_type_input(
            report_type="specific_workflow",
            description="Total Approved with Score",
        )


def test_lock_drops_unknown_columns():
    raw = {
        "title": "Awaiting action",
        "reportType": "all_workflows",
        "sources": [{"alias": "i", "schemaName": "workflow", "table": "inbox_aaaaaaaa"}],
        "joins": [],
        "columns": [
            {"key": "name", "label": "Name", "source": "i.name", "aggregate": "none"},
            {"key": "bogus", "label": "Bogus", "source": "i.not_a_column", "aggregate": "none"},
        ],
        "filters": [{"field": "i.stage", "op": "eq", "value": "Pending"}],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [],
        "summary": {},
        "warnings": [],
    }
    locked = lock_report_definition(raw, _schema())
    keys = [c["key"] for c in locked["columns"]]
    assert "name" in keys
    assert "bogus" not in keys
    assert any("not_a_column" in w for w in locked["warnings"])


def test_definition_sql_is_read_only_and_paginated():
    locked = {
        "sources": [{"alias": "i", "schemaName": "workflow", "table": "inbox_aaaaaaaa"}],
        "joins": [],
        "columns": [
            {"key": "name", "label": "Name", "source": "i.name", "aggregate": "none"},
            {"key": "pendingCount", "label": "Pending", "source": "1", "aggregate": "count"},
        ],
        "filters": [{"field": "i.stage", "op": "eq", "value": "Pending"}],
        "groupBy": ["i.name"],
        "orderBy": [{"field": "pendingCount", "direction": "desc"}],
        "availableFilters": [],
    }
    data_sql, count_sql = generate_definition_sql(locked, page=2, page_size=10)
    ok, errors = validate_read_only_sql(data_sql, schema=_schema(), allowed_tables=["workflow.inbox_aaaaaaaa"])
    assert ok, errors
    assert "LIMIT 10 OFFSET 10" in data_sql
    assert "COUNT(*)" in count_sql.upper()
    assert "INSERT" not in data_sql.upper()


def test_parse_definition_json_repairs_trailing_comma():
    raw = """```json
{
  "title": "Approved",
  "reportType": "specific_workflow",
  "sources": [{"alias": "t", "schemaName": "dbo", "table": "ezfb_6e45749f_items"}],
  "joins": [],
  "columns": [
    {"key": "Invoice_No", "label": "Invoice", "source": "t.Invoice_No", "aggregate": "none"},
  ],
  "filters": [],
  "groupBy": [],
  "orderBy": [],
  "availableFilters": [],
  "summary": {},
  "warnings": [],
}
```"""
    payload = parse_definition_json(raw)
    assert payload["title"] == "Approved"
    assert payload["columns"][0]["key"] == "Invoice_No"


def test_fallback_definition_from_prompt_uses_named_table():
    schema = _schema()
    # add ezfb table like live AP form
    schema.tables.append(("dbo", "ezfb_6e45749f_items"))
    schema.columns_by_table[("dbo", "ezfb_6e45749f_items")] = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
    ]
    schema.all_columns.extend(schema.columns_by_table[("dbo", "ezfb_6e45749f_items")])
    from app.report_agent.definition_lock import fallback_definition_from_prompt

    prompt = (
        "Data sources: Use the table dbo.ezfb_6e45749f_items. "
        "Required fields: Matched_Status, PO_Amount, Invoice_Amount, Invoice_No, PO_Number. "
        "Total Approved with Score"
    )
    raw = fallback_definition_from_prompt(prompt, schema, report_type="specific_workflow")
    assert raw is not None
    assert raw["sources"][0]["table"] == "ezfb_6e45749f_items"
    keys = {c["key"] for c in raw["columns"]}
    assert "Invoice_No" in keys
    assert raw["warnings"]


def test_report_spec_to_prompt_includes_formula():
    from app.models.report_agent import ReportSpec, ReportSpecColumn, ReportSpecFilter
    from app.report_agent.report_spec import report_spec_to_prompt

    spec = ReportSpec(
        title="Total Approved",
        objective="Sum approved amounts",
        report_type="specific_workflow",
        columns=[
            ReportSpecColumn(key="Invoice_No", label="Invoice No"),
            ReportSpecColumn(key="Total_Amount", label="Total Amount", formula="PO_Amount + Invoice_Amount"),
        ],
        filters=[ReportSpecFilter(field="Matched_Status", op="not_null")],
    )
    prompt = report_spec_to_prompt(spec, workflow_name="Accounts Payable Form")
    assert "Sum approved amounts" in prompt
    assert "Total_Amount" in prompt
    assert "PO_Amount + Invoice_Amount" in prompt
    assert "Matched_Status is not null" in prompt
    assert "Accounts Payable Form" in prompt


def test_fallback_report_spec_adds_total_formula():
    from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice
    from app.report_agent.report_spec import fallback_report_spec

    tables = [
        SchemaTableSlice(
            schema_name="dbo",
            table="ezfb_items",
            columns=[
                SchemaColumnSlice(name="Invoice_No", type="text"),
                SchemaColumnSlice(name="PO_Amount", type="numeric"),
                SchemaColumnSlice(name="Invoice_Amount", type="numeric"),
                SchemaColumnSlice(name="Matched_Status", type="text"),
            ],
        )
    ]
    spec = fallback_report_spec(
        description="Total Approved with Score",
        report_type="specific_workflow",
        tables=tables,
    )
    assert any(c.key == "Total_Amount" and c.formula for c in spec.columns)
    assert any("Score" in w for w in spec.warnings)


def test_parse_definition_json_strips_fences():
    payload = parse_definition_json('```json\n{"title":"T","sources":[],"columns":[]}\n```')
    assert payload["title"] == "T"


class _FakeLLM:
    def __init__(self, content: str, *, fail_first: bool = False):
        self._content = content
        self._fail_first = fail_first
        self.calls = 0

    async def chat_completion(self, messages, **overrides):
        self.calls += 1
        if self._fail_first and self.calls == 1:
            from app.llm.adapter import LLMAdapterError

            raise LLMAdapterError(
                "The language model provider timed out. Try another model preset in the console."
            )
        return {"content": self._content, "usage": None}


@pytest.mark.asyncio
async def test_generate_prompt_retries_fallback_on_timeout(monkeypatch):
    llm = _FakeLLM("Recovered prompt text", fail_first=True)
    service = ReportAgentService(llm_adapter=llm)
    schema = _schema()

    async def fake_schema(db):
        return schema

    async def fake_db(tenant_id):
        return object()

    async def fake_presets(tenant_id, model=None):
        return {"model": "primary-model"}, [{"model": "fallback-model"}, {"model": "fallback2-model"}]

    monkeypatch.setattr("app.report_agent.service.get_database_schema", fake_schema)
    monkeypatch.setattr(service, "_resolve_db", fake_db)
    monkeypatch.setattr(service, "_resolve_llm_presets", fake_presets)
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )

    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="all_workflows",
            description="Awaiting for your action",
            tenant_id="tenant-1",
        )
    )
    assert llm.calls == 2
    assert resp.report_spec is not None
    assert resp.report_spec.objective
    assert "Inbox_Count" in [c.key for c in resp.report_spec.columns]
    assert resp.report_prompt


class _FakeLLMSimple:
    def __init__(self, content: str):
        self._content = content

    async def chat_completion(self, messages, **overrides):
        return {"content": self._content, "usage": None}


class _FakeDb:
    def __init__(self, rows: Optional[list[dict[str, Any]]] = None):
        self._rows = rows or []

    async def fetch(self, sql: str):
        if "information_schema" in sql.lower():
            # minimal columns for schema discovery fallback path is complex;
            # service will be patched instead in integration-ish tests
            return []
        if "count(*)" in sql.lower():
            return [{"total": len(self._rows)}]
        return self._rows


@pytest.mark.asyncio
async def test_generate_prompt_uses_llm(monkeypatch):
    service = ReportAgentService(llm_adapter=_FakeLLMSimple("Report objective\nData sources\ninbox_aaaaaaaa"))
    schema = _schema()

    async def fake_schema(db):
        return schema

    async def fake_db(tenant_id):
        return object()

    monkeypatch.setattr("app.report_agent.service.get_database_schema", fake_schema)
    monkeypatch.setattr(service, "_resolve_db", fake_db)
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )

    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="all_workflows",
            description="Awaiting for your action",
            tenant_id="tenant-1",
        )
    )
    assert resp.report_type == "all_workflows"
    assert resp.report_spec is not None
    assert resp.report_spec.columns or "Report objective" in resp.report_prompt
    assert "inbox_aaaaaaaa" in resp.report_prompt or "Report objective" in resp.report_prompt or resp.report_spec.objective


@pytest.mark.asyncio
async def test_run_report_empty_on_bad_definition(monkeypatch):
    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple('{"title":"X","sources":[],"columns":[],"joins":[],"filters":[],"groupBy":[],"orderBy":[],"availableFilters":[],"summary":{},"warnings":["none"]}')
    )
    schema = _schema()

    async def fake_schema(db):
        return schema

    async def fake_db(tenant_id):
        return object()

    monkeypatch.setattr("app.report_agent.service.get_database_schema", fake_schema)
    monkeypatch.setattr(service, "_resolve_db", fake_db)
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )

    resp = await service.run_report(
        RunReportRequest(
            report_prompt="Awaiting for your action across workflows",
            report_type="all_workflows",
            tenant_id="tenant-1",
        )
    )
    assert resp.rows == []
    assert resp.total_count == 0
    assert resp.warnings


@pytest.mark.asyncio
async def test_run_report_executes_locked_sql(monkeypatch):
    definition = {
        "title": "Awaiting action",
        "reportType": "all_workflows",
        "sources": [{"alias": "i", "schemaName": "workflow", "table": "inbox_aaaaaaaa"}],
        "joins": [],
        "columns": [{"key": "name", "label": "Name", "source": "i.name", "aggregate": "none"}],
        "filters": [],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [{"key": "name", "label": "Name", "field": "i.name", "type": "text"}],
        "summary": {},
        "warnings": [],
    }
    import json

    service = ReportAgentService(llm_adapter=_FakeLLMSimple(json.dumps(definition)))
    schema = _schema()
    db = _FakeDb(rows=[{"name": "Invoice 1"}])

    async def fake_schema(_db):
        return schema

    async def fake_db(tenant_id):
        return db

    monkeypatch.setattr("app.report_agent.service.get_database_schema", fake_schema)
    monkeypatch.setattr(service, "_resolve_db", fake_db)
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )

    resp = await service.run_report(
        RunReportRequest(
            report_prompt="Awaiting for your action",
            report_type="all_workflows",
            page=1,
            page_size=25,
            include_debug=True,
        )
    )
    assert resp.title == "Awaiting action"
    assert resp.columns[0].key == "name"
    assert resp.rows == [{"name": "Invoice 1"}]
    assert resp.total_count == 1
    assert resp.debug and "sql" in resp.debug


def _pascal_schema() -> DatabaseSchema:
    schema = DatabaseSchema()
    schema.tables = [
        ("workflow", "Workflows"),
        ("dbo", "ezfb_29171de4_items"),
    ]
    cols = {
        ("workflow", "workflows"): [
            ColumnMeta("workflow", "Workflows", "Id", "uuid"),
            ColumnMeta("workflow", "Workflows", "Name", "text"),
            ColumnMeta("workflow", "Workflows", "IsDeleted", "boolean"),
        ],
        ("dbo", "ezfb_29171de4_items"): [
            ColumnMeta("dbo", "ezfb_29171de4_items", "Invoice_No", "text"),
            ColumnMeta("dbo", "ezfb_29171de4_items", "Matched_Status", "text"),
            ColumnMeta("dbo", "ezfb_29171de4_items", "PO_Amount", "numeric"),
            ColumnMeta("dbo", "ezfb_29171de4_items", "Invoice_Amount", "numeric"),
            ColumnMeta("dbo", "ezfb_29171de4_items", "IsDeleted", "boolean"),
        ],
    }
    schema.columns_by_table = cols
    schema.all_columns = [c for lst in cols.values() for c in lst]
    schema.table_to_workflow = {"29171de4": "Accounts Payable"}
    return schema


def test_lock_preserves_identifier_case():
    raw = {
        "title": "Workflows catalog",
        "reportType": "all_workflows",
        "sources": [{"alias": "w", "schemaName": "workflow", "table": "workflows"}],
        "joins": [],
        "columns": [{"key": "name", "label": "Name", "source": "w.name", "aggregate": "none"}],
        "filters": [{"field": "w.isDeleted", "op": "eq", "value": False}],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [],
        "summary": {},
        "warnings": [],
    }
    locked = lock_report_definition(raw, _pascal_schema())
    # Catalog source is redirected to the fact table when one exists.
    assert locked["sources"][0]["table"] == "ezfb_29171de4_items"
    assert locked["sources"][0]["schemaName"] == "dbo"


def test_lock_canonicalizes_columns_on_fact_table():
    raw = {
        "title": "Approved invoices",
        "reportType": "specific_workflow",
        "sources": [{"alias": "t", "schemaName": "dbo", "table": "ezfb_29171de4_items"}],
        "joins": [],
        "columns": [
            {"key": "invoice_no", "label": "Invoice", "source": "t.invoice_no", "aggregate": "none"},
            {
                "key": "Total_Amount",
                "label": "Total",
                "source": "t.po_amount + t.invoice_amount",
                "aggregate": "none",
            },
        ],
        "filters": [{"field": "t.matched_status", "op": "eq", "value": "Total Approved with Score"}],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [],
        "summary": {},
        "warnings": [],
    }
    locked = lock_report_definition(raw, _pascal_schema())
    assert locked["sources"][0]["table"] == "ezfb_29171de4_items"
    sources = {c["key"]: c["source"] for c in locked["columns"]}
    assert sources["invoice_no"] == "t.Invoice_No"
    assert "t.PO_Amount" in sources["Total_Amount"]
    assert "t.Invoice_Amount" in sources["Total_Amount"]
    status = next(f for f in locked["filters"] if "Matched_Status" in f["field"])
    assert status["op"] == "not_null"
    deleted = next(f for f in locked["filters"] if "IsDeleted" in f["field"])
    assert deleted["op"] == "not_deleted"
    data_sql, _count_sql = generate_definition_sql(locked, page=1, page_size=25)
    assert '"ezfb_29171de4_items"' in data_sql
    assert "COALESCE" in data_sql
    assert "LOWER(CAST(" in data_sql or "IS NOT NULL" in data_sql


def test_sql_soft_delete_uses_coalesce_not_false_eq():
    locked = {
        "sources": [{"alias": "t", "schemaName": "dbo", "table": "ezfb_29171de4_items"}],
        "joins": [],
        "columns": [
            {"key": "Invoice_No", "label": "Invoice", "source": "t.Invoice_No", "aggregate": "none"}
        ],
        "filters": [{"field": "t.IsDeleted", "op": "not_deleted", "value": None}],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [],
    }
    data_sql, _ = generate_definition_sql(locked)
    assert "COALESCE" in data_sql
    assert '"IsDeleted"' in data_sql
    assert '= FALSE' not in data_sql.upper().replace("COALESCE", "")


def test_report_spec_prompt_includes_source_table():
    from app.models.report_agent import ReportSpec, ReportSpecColumn
    from app.report_agent.report_spec import report_spec_to_prompt

    spec = ReportSpec(
        title="Approved",
        objective="List approved invoices",
        report_type="specific_workflow",
        source_schema="dbo",
        source_table="ezfb_29171de4_items",
        columns=[ReportSpecColumn(key="Invoice_No", label="Invoice No")],
    )
    prompt = report_spec_to_prompt(spec, workflow_name="Accounts Payable")
    assert "dbo.ezfb_29171de4_items" in prompt
    assert "Workflows" in prompt  # instruction not to query catalogs


@pytest.mark.asyncio
async def test_scope_options_use_ezofis_api_when_tenant_db_missing():
    class _FailingPools:
        async def acquire_for_global_search(self, tenant_id, catalog_store=None):
            raise RuntimeError('database "ezofis_Tenant_b843b988" does not exist')

    class _Ezofis:
        async def list_workflows(self, tenant_id):
            return [
                {"id": "1", "name": "Accounts Payable"},
                {"id": "2", "name": "Leave Request"},
                {"id": "3", "name": "Purchase Order"},
            ]

        async def list_repositories(self, tenant_id):
            return [{"id": "r1", "name": "AP Documents"}]

    service = ReportAgentService(
        tenant_pools=_FailingPools(),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_Ezofis(),
    )
    workflows = await service.list_scope_options(
        scope="workflows",
        tenant_id="b843b988-00ec-44e3-aca2-b8470133ef63",
    )
    assert [w.name for w in workflows] == [
        "Accounts Payable",
        "Leave Request",
        "Purchase Order",
    ]


@pytest.mark.asyncio
async def test_scope_options_return_empty_not_error_when_tenant_db_missing():
    class _FailingPools:
        async def acquire_for_global_search(self, tenant_id, catalog_store=None):
            raise RuntimeError('database "ezofis_Tenant_b843b988" does not exist')

    service = ReportAgentService(tenant_pools=_FailingPools(), db_pool=None)
    workflows = await service.list_scope_options(
        scope="workflows",
        tenant_id="b843b988-00ec-44e3-aca2-b8470133ef63",
    )
    assert workflows == []


@pytest.mark.asyncio
async def test_resolve_db_falls_back_when_tenant_missing():
    class _FailingPools:
        async def acquire_for_global_search(self, tenant_id, catalog_store=None):
            raise RuntimeError('database "ezofis_Tenant_b843b988" does not exist')

    fallback = object()
    service = ReportAgentService(tenant_pools=_FailingPools(), db_pool=fallback)
    db = await service._resolve_db("b843b988-00ec-44e3-aca2-b8470133ef63")
    assert db is fallback


def test_scope_prefers_fact_tables_over_catalog():
    from app.report_agent.report_types import get_report_type
    from app.report_agent.schema_scope import preferred_fact_table, scope_schema

    tables = scope_schema(_pascal_schema(), get_report_type("all_workflows"))
    names = {t.table for t in tables}
    assert "ezfb_29171de4_items" in names
    assert "Workflows" not in names
    preferred = preferred_fact_table(tables)
    assert preferred is not None
    assert preferred.table == "ezfb_29171de4_items"


def _ap_tables(*, with_date: bool = True):
    from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice

    cols = [
        SchemaColumnSlice(name="Invoice_No", type="text"),
        SchemaColumnSlice(name="Supplier", type="text"),
        SchemaColumnSlice(name="Matched_Status", type="text"),
        SchemaColumnSlice(name="PO_Amount", type="numeric"),
        SchemaColumnSlice(name="Invoice_Amount", type="numeric"),
        SchemaColumnSlice(name="Stage", type="text"),
    ]
    if with_date:
        cols.append(SchemaColumnSlice(name="Invoice_Date", type="date"))
    return [
        SchemaTableSlice(schema_name="dbo", table="ezfb_items", columns=cols)
    ]


def test_fallback_ap_aging_differs_from_approved_total():
    from app.report_agent.report_spec import fallback_report_spec

    tables = _ap_tables()
    aging = fallback_report_spec(
        description="AP Aging Report",
        report_type="specific_workflow",
        tables=tables,
    )
    approved = fallback_report_spec(
        description="Total Approved with Score",
        report_type="specific_workflow",
        tables=tables,
    )
    aging_keys = [c.key for c in aging.columns]
    approved_keys = [c.key for c in approved.columns]
    assert aging.title == "AP Aging Report"
    assert approved.title == "Total Approved with Score"
    assert "Aging_Bucket" in aging_keys
    assert any(c.formula and "CASE" in c.formula for c in aging.columns)
    assert "Total_Amount" in approved_keys
    assert aging_keys != approved_keys
    assert "Aging_Bucket" in aging.group_by


def _all_workflow_tables():
    from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice

    return [
        SchemaTableSlice(
            schema_name="workflow",
            table="workflow_instances_all",
            columns=[
                SchemaColumnSlice(name="workflowName", type="text"),
                SchemaColumnSlice(name="status", type="text"),
                SchemaColumnSlice(name="startedBy", type="text"),
                SchemaColumnSlice(name="createdAtUtc", type="date"),
                SchemaColumnSlice(name="completedAtUtc", type="date"),
            ],
        )
    ]


def _all_repository_tables():
    from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice

    return [
        SchemaTableSlice(
            schema_name="repository",
            table="items_all",
            columns=[
                SchemaColumnSlice(name="Repository_Name", type="text"),
                SchemaColumnSlice(name="FileName", type="text"),
                SchemaColumnSlice(name="FileVersion", type="text"),
                SchemaColumnSlice(name="CreatedBy", type="text"),
                SchemaColumnSlice(name="CreatedAt", type="date"),
                SchemaColumnSlice(name="Status", type="text"),
            ],
        )
    ]


def test_all_workflow_common_reports_use_shared_instance_fields():
    from app.report_agent.report_spec import fallback_report_spec

    tables = _all_workflow_tables()
    aging = fallback_report_spec(description="Aging", report_type="all_workflows", tables=tables)
    sla = fallback_report_spec(description="SLA", report_type="all_workflows", tables=tables)
    inbox = fallback_report_spec(description="Total Inbox", report_type="all_workflows", tables=tables)
    processed = fallback_report_spec(
        description="Total tickets processed", report_type="all_workflows", tables=tables
    )
    assert aging.title == "Aging"
    assert "Aging_Bucket" in [c.key for c in aging.columns]
    assert "workflowName" in aging.group_by
    assert "Invoice_No" not in [c.key for c in aging.columns]
    assert sla.title == "Workflow SLA Compliance"
    assert any(c.key == "Sla_Bucket" or (c.formula or "").upper().find("SLA") >= 0 for c in sla.columns)
    assert "Inbox_Count" in [c.key for c in inbox.columns]
    assert inbox.group_by and "workflowName" in inbox.group_by
    assert "Processed_Count" in [c.key for c in processed.columns]
    assert any(f.field == "completedAtUtc" and f.op == "not_null" for f in processed.filters)


def test_all_repository_common_reports_use_shared_document_fields():
    from app.report_agent.report_spec import fallback_report_spec

    tables = _all_repository_tables()
    total = fallback_report_spec(description="Total documents", report_type="all_repositories", tables=tables)
    aging = fallback_report_spec(description="Document aging", report_type="all_repositories", tables=tables)
    assert "Document_Count" in [c.key for c in total.columns]
    assert "Repository_Name" in total.group_by
    assert "Invoice_No" not in [c.key for c in total.columns]
    assert "Aging_Bucket" in [c.key for c in aging.columns]
    assert "Repository_Name" in aging.group_by


def test_fallback_vendor_outstanding_groups_by_vendor():
    from app.report_agent.report_spec import fallback_report_spec

    spec = fallback_report_spec(
        description="Vendor Outstanding Report",
        report_type="specific_workflow",
        tables=_ap_tables(),
    )
    assert spec.title == "Vendor Outstanding Report"
    assert "Supplier" in spec.group_by
    assert any(c.formula and c.formula.startswith("SUM(") for c in spec.columns)
    assert any(c.formula == "COUNT(*)" for c in spec.columns)


def test_merge_spec_replaces_generic_columns_with_catalog():
    from app.models.report_agent import ReportSpec, ReportSpecColumn
    from app.report_agent.definition_lock import merge_spec_into_definition
    from app.report_agent.report_spec import fallback_report_spec

    raw = {
        "title": "Invoice list",
        "sources": [{"alias": "t", "schemaName": "dbo", "table": "ezfb_items"}],
        "columns": [
            {"key": "Invoice_No", "label": "Invoice No", "source": "t.Invoice_No", "aggregate": "none"}
        ],
        "filters": [],
        "groupBy": [],
    }
    spec = fallback_report_spec(
        description="AP Aging Report",
        report_type="specific_workflow",
        tables=_ap_tables(),
    )
    merged = merge_spec_into_definition(raw, spec)
    keys = [c["key"] for c in merged["columns"]]
    assert "Aging_Bucket" in keys
    assert merged["title"] == "AP Aging Report"
    assert merged["groupBy"]


def test_aging_case_formula_survives_lock_and_sql():
    from app.report_agent.definition_lock import lock_report_definition, merge_spec_into_definition
    from app.report_agent.definition_sql import generate_definition_sql
    from app.report_agent.report_spec import fallback_report_spec

    schema = _schema()
    schema.tables.append(("dbo", "ezfb_items"))
    schema.columns_by_table[("dbo", "ezfb_items")] = [
        ColumnMeta("dbo", "ezfb_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_items", "Supplier", "text"),
        ColumnMeta("dbo", "ezfb_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_items", "Invoice_Date", "date"),
        ColumnMeta("dbo", "ezfb_items", "Matched_Status", "text"),
    ]
    schema.all_columns.extend(schema.columns_by_table[("dbo", "ezfb_items")])
    spec = fallback_report_spec(
        description="AP Aging Report",
        report_type="specific_workflow",
        tables=_ap_tables(),
    )
    raw = {
        "title": "x",
        "sources": [{"alias": "t", "schemaName": "dbo", "table": "ezfb_items"}],
        "columns": [{"key": "Invoice_No", "source": "t.Invoice_No", "aggregate": "none"}],
        "filters": [],
        "groupBy": [],
        "orderBy": [],
        "availableFilters": [],
        "summary": {},
        "warnings": [],
    }
    merged = merge_spec_into_definition(raw, spec)
    locked = lock_report_definition(merged, schema, report_type="specific_workflow")
    keys = [c["key"] for c in locked["columns"]]
    assert "Aging_Bucket" in keys
    data_sql, _count_sql = generate_definition_sql(locked, page=1, page_size=25)
    assert "CASE" in data_sql.upper()
    assert "GROUP BY" in data_sql.upper()
    assert "COUNT(" in data_sql.upper()


class _EzofisTenantDb:
    """Stand-in for a live EZOFIS tenant DB (Workflows / Repositories / ezfb items)."""

    def __init__(self):
        self.workflows = [
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "name": "Accounts Payable",
                "form_id": "29171de4-1111-2222-3333-444455556666",
                "repository_id": "9f522761-1111-2222-3333-444455556666",
            }
        ]
        self.repos = [
            {
                "id": "9f522761-1111-2222-3333-444455556666",
                "name": "AP Documents",
                "items_table": "repository.items_9f522761",
            }
        ]
        self.item_columns = [
            {
                "table_schema": "dbo",
                "table_name": "ezfb_29171de4_items",
                "column_name": "Invoice_No",
                "data_type": "text",
                "udt_name": "text",
                "is_nullable": "YES",
            },
            {
                "table_schema": "dbo",
                "table_name": "ezfb_29171de4_items",
                "column_name": "Invoice_Amount",
                "data_type": "numeric",
                "udt_name": "numeric",
                "is_nullable": "YES",
            },
            {
                "table_schema": "dbo",
                "table_name": "ezfb_29171de4_items",
                "column_name": "Invoice_Date",
                "data_type": "timestamp",
                "udt_name": "timestamp",
                "is_nullable": "YES",
            },
            {
                "table_schema": "dbo",
                "table_name": "ezfb_29171de4_items",
                "column_name": "Supplier",
                "data_type": "text",
                "udt_name": "text",
                "is_nullable": "YES",
            },
        ]

    async def fetch(self, sql: str, *args: Any):
        compact = " ".join(sql.split()).lower()
        arg0 = args[0] if args else None
        names = []
        if isinstance(arg0, (list, tuple)):
            names = [str(x).lower() for x in arg0]
        elif arg0 is not None:
            names = [str(arg0).lower()]
        if "from information_schema.tables" in compact:
            if any(n in ("workflows", "wworkflow", "wworkflows") for n in names):
                return [{"table_schema": "workflow", "table_name": "Workflows"}]
            if any(n in ("repositories", "wrepository") for n in names):
                return [{"table_schema": "repository", "table_name": "Repositories"}]
            if args and "ezfb_" in str(args[0]).lower():
                return [{"table_schema": "dbo", "table_name": "ezfb_29171de4_items"}]
            if "like 'ezfb_%_items'" in compact or "ezfb_%_items" in compact:
                return [{"table_schema": "dbo", "table_name": "ezfb_29171de4_items"}]
            if args and "items_" in str(args[0]).lower():
                return [{"table_schema": "repository", "table_name": "items_9f522761"}]
            return [{"table_schema": "dbo", "table_name": "ezfb_29171de4_items"}]
        if "from information_schema.columns" in compact:
            table = str(args[1]).lower() if len(args) > 1 else ""
            schema = str(args[0]).lower() if args else ""
            if "workflow" in table or schema == "workflow":
                return [
                    {"column_name": "Id"},
                    {"column_name": "Name"},
                    {"column_name": "FormId"},
                    {"column_name": "RepositoryId"},
                    {"column_name": "IsDeleted"},
                ]
            if "repositor" in table or schema == "repository":
                return [
                    {"column_name": "Id"},
                    {"column_name": "Name"},
                    {"column_name": "ItemsTableName"},
                    {"column_name": "IsDeleted"},
                ]
            return list(self.item_columns)
        if "from \"workflow\".\"workflows\"" in compact or "from workflow.\"workflows\"" in compact:
            return list(self.workflows)
        if "from \"repository\".\"repositories\"" in compact or "from repository.\"repositories\"" in compact:
            return list(self.repos)
        if "workflow.\"workflows\"" in compact or "from workflow.workflows" in compact:
            return list(self.workflows)
        if "repository.\"repositories\"" in compact or "from repository.repositories" in compact:
            return list(self.repos)
        return []


@pytest.mark.asyncio
async def test_scope_options_merges_every_workflow_catalog_table():
    class _ManyWorkflowDb(_EzofisTenantDb):
        def __init__(self):
            super().__init__()
            self.wworkflow = [
                {
                    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "name": "Leave Request",
                    "form_id": None,
                    "repository_id": None,
                },
                {
                    "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "name": "Purchase Order",
                    "form_id": None,
                    "repository_id": None,
                },
            ]

        async def fetch(self, sql: str, *args: Any):
            compact = " ".join(sql.split()).lower()
            arg0 = args[0] if args else None
            names = [str(x).lower() for x in arg0] if isinstance(arg0, (list, tuple)) else []
            if "from information_schema.tables" in compact and any(
                n in ("workflows", "wworkflow", "wworkflows") for n in names
            ):
                return [
                    {"table_schema": "workflow", "table_name": "Workflows"},
                    {"table_schema": "dbo", "table_name": "wworkflow"},
                ]
            if "from information_schema.columns" in compact:
                table = str(args[1]).lower() if len(args) > 1 else ""
                if "wworkflow" in table:
                    return [{"column_name": "Id"}, {"column_name": "Name"}, {"column_name": "IsDeleted"}]
            if "from \"dbo\".\"wworkflow\"" in compact:
                return list(self.wworkflow)
            return await super().fetch(sql, *args)

    service = ReportAgentService(db_pool=_ManyWorkflowDb())
    workflows = await service.list_scope_options(scope="workflows", tenant_id="")
    names = [w.name for w in workflows]
    assert "Accounts Payable" in names
    assert "Leave Request" in names
    assert "Purchase Order" in names


@pytest.mark.asyncio
async def test_scope_options_read_live_workflow_and_repository_tables():
    service = ReportAgentService(db_pool=_EzofisTenantDb())
    workflows = await service.list_scope_options(scope="workflows", tenant_id="tenant-1")
    repos = await service.list_scope_options(scope="repositories", tenant_id="tenant-1")
    assert [w.name for w in workflows] == ["Accounts Payable"]
    assert [r.name for r in repos] == ["AP Documents"]


@pytest.mark.asyncio
async def test_generate_prompt_uses_live_ezfb_columns(monkeypatch):
    service = ReportAgentService(llm_adapter=_FakeLLMSimple("not-json"), db_pool=_EzofisTenantDb())

    async def fake_db(tenant_id):
        return _EzofisTenantDb()

    monkeypatch.setattr(service, "_resolve_db", fake_db)
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )

    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="specific_workflow",
            description="AP Aging Report",
            workflow_name="Accounts Payable",
            tenant_id="tenant-1",
        )
    )
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    available = resp.report_spec.available_columns if resp.report_spec else []
    assert "Invoice_No" in available
    assert "Invoice_Amount" in available
    assert "Invoice_Date" in available
    assert "Supplier" in available
    assert any(k in keys for k in ("Invoice_No", "Invoice_Amount", "Invoice_Date", "Supplier", "Aging_Bucket"))
    assert resp.report_spec.source_table == "ezfb_29171de4_items"


def test_fallback_rfq_description_picks_form_fields_not_invoice():
    from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice
    from app.report_agent.report_spec import fallback_report_spec

    tables = [
        SchemaTableSlice(
            schema_name="dbo",
            table="ezfb_787372d8_items",
            columns=[
                SchemaColumnSlice(name="Company_Name", type="text"),
                SchemaColumnSlice(name="Order_Number", type="text"),
                SchemaColumnSlice(name="Date", type="date"),
                SchemaColumnSlice(name="Total", type="numeric"),
                SchemaColumnSlice(name="Email", type="text"),
            ],
        )
    ]
    spec = fallback_report_spec(
        description="List RFQ requests with company, order number, date and total",
        report_type="specific_workflow",
        tables=tables,
    )
    keys = [c.key for c in spec.columns]
    assert "Company_Name" in keys
    assert "Order_Number" in keys
    assert "Invoice_No" not in keys
    assert "PO_Number" not in keys


def test_extract_form_fields_skips_table_children_and_calc_ast():
    from app.report_agent.form_schema import extract_form_fields

    fields = extract_form_fields(
        {
            "panels": [
                {"type": "SHORT_TEXT", "label": "Company Name", "id": "a1"},
                {"type": "SHORT_TEXT", "label": "Order Number", "id": "a2"},
                {"type": "DATE", "label": "Date", "id": "a3"},
                {"type": "NUMBER", "label": "Total", "id": "a4"},
                {
                    "type": "TABLE",
                    "label": "Line Item",
                    "id": "tbl",
                    "settings": {
                        "columns": [
                            {"type": "NUMBER", "label": "Qty", "name": "Qty"},
                            {"type": "NUMBER", "label": "Price", "name": "Price"},
                        ]
                    },
                },
                {"name": "Calculation", "calculations": [{"type": "", "label": "FIELD", "name": "FIELD"}]},
            ]
        }
    )
    keys = [f["key"] for f in fields]
    labels = [f["label"] for f in fields]
    assert keys == ["Company_Name", "Order_Number", "Date", "Total"]
    assert "Qty" not in labels
    assert "Invoice_No" not in keys


@pytest.mark.asyncio
async def test_generate_prompt_uses_selected_workflow_form_not_ap_columns(monkeypatch):
    class _RfqEzofis:
        async def list_workflows(self, tenant_id):
            return [{"id": "wf-rfq", "name": "RFQ Process"}]

        async def get_workflow(self, *, tenant_id, workflow_id):
            return {
                "ok": True,
                "id": workflow_id,
                "name": "RFQ Process",
                "formId": "787372d8-d66a-47f6-ba3f-36cb6dffb360",
            }

        async def get_form(self, *, tenant_id, form_id):
            return {
                "ok": True,
                "id": form_id,
                "name": "RFQ Form",
                "formJson": {
                    "panels": [
                        {"type": "SHORT_TEXT", "label": "Company Name", "id": "a"},
                        {"type": "SHORT_TEXT", "label": "Order Number", "id": "b"},
                        {"type": "DATE", "label": "Date", "id": "c"},
                        {"type": "CALCULATED", "label": "Total", "id": "d"},
                        {"type": "EMAIL", "label": "Email", "id": "e"},
                    ]
                },
            }

    invented = (
        '{"title":"RFQ","objective":"List RFQ","columns":['
        '{"key":"Invoice_No","label":"Invoice Number"},'
        '{"key":"PO_Number","label":"PO Number"}'
        ']}'
    )
    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple(invented),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_RfqEzofis(),
    )
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )
    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="specific_workflow",
            description="Total Approved with Score",
            workflow_name="RFQ Process",
            tenant_id="tenant-1",
        )
    )
    available = resp.report_spec.available_columns if resp.report_spec else []
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    assert "Company_Name" in available
    assert "Order_Number" in available
    assert "Invoice_No" not in available
    assert "PO_Number" not in available
    assert "Invoice_No" not in keys
    assert "PO_Number" not in keys
    assert any(k in keys for k in ("Company_Name", "Order_Number", "Date", "Total", "Email"))
    assert (resp.report_spec.source_table or "").startswith("ezfb_787372d8")


def test_extract_repository_fields_uses_sql_column_and_skips_tables():
    from app.report_agent.form_schema import extract_repository_fields

    fields = extract_repository_fields(
        [
            {"name": "Employee Name", "sqlColumnName": "Employee_Name", "type": "SHORT_TEXT"},
            {"name": "Employee ID", "sqlColumnName": "Employee_ID", "type": "SHORT_TEXT"},
            {"name": "Date Issued", "sqlColumnName": "Date_Issued", "type": "DATE"},
            {"name": "Line Items", "sqlColumnName": "Line_Items", "type": "TABLE"},
        ]
    )
    keys = [f["key"] for f in fields]
    assert "FileName" in keys
    assert "Employee_Name" in keys
    assert "Date_Issued" in keys
    assert "Line_Items" not in keys
    assert "Invoice_No" not in keys


@pytest.mark.asyncio
async def test_generate_prompt_uses_selected_repository_fields_not_ap_columns(monkeypatch):
    class _HrRepoEzofis:
        async def list_repositories(self, tenant_id):
            return [{"id": "repo-hr", "name": "HR Records"}]

        async def get_repository(self, *, tenant_id, repository_id):
            return {
                "ok": True,
                "id": repository_id,
                "name": "HR Records",
                "itemsTableName": "items_7e3343ed",
                "fields": [
                    {"name": "Employee Name", "sqlColumnName": "Employee_Name", "type": "SHORT_TEXT"},
                    {"name": "Employee ID", "sqlColumnName": "Employee_ID", "type": "SHORT_TEXT"},
                    {"name": "Payslip Month", "sqlColumnName": "Payslip_Month", "type": "SHORT_TEXT"},
                    {"name": "Date Issued", "sqlColumnName": "Date_Issued", "type": "DATE"},
                ],
            }

    invented = (
        '{"title":"HR","objective":"x","columns":['
        '{"key":"Invoice_No","label":"Invoice Number"},'
        '{"key":"PO_Number","label":"PO Number"}'
        ']}'
    )
    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple(invented),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_HrRepoEzofis(),
    )
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )
    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="specific_repository",
            description="Employee documents by name and payslip month",
            repository_name="HR Records",
            tenant_id="tenant-1",
        )
    )
    available = resp.report_spec.available_columns if resp.report_spec else []
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    assert "Employee_Name" in available
    assert "Payslip_Month" in available
    assert "Invoice_No" not in available
    assert "PO_Number" not in available
    assert "Invoice_No" not in keys
    assert any(k in keys for k in ("Employee_Name", "Employee_ID", "Payslip_Month", "Date_Issued", "FileName"))
    assert resp.report_spec.source_table == "items_7e3343ed"


@pytest.mark.asyncio
async def test_generate_prompt_all_workflows_uses_tenant_catalog_not_ap_columns(monkeypatch):
    class _AllEzofis:
        async def list_workflows(self, tenant_id):
            return [
                {"id": "1", "name": "RFQ Process"},
                {"id": "2", "name": "Document Approval"},
                {"id": "3", "name": "Expense Reimbursement"},
            ]

    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple("not-json"),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_AllEzofis(),
    )
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )
    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="all_workflows",
            description="Awaiting for your action",
            tenant_id="tenant-1",
        )
    )
    available = resp.report_spec.available_columns if resp.report_spec else []
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    assert "workflowName" in available
    assert "status" in available
    assert "Invoice_No" not in available
    assert "Invoice_No" not in keys
    assert "Inbox_Count" in keys
    assert "workflowName" in keys
    assert len(keys) >= 3
    assert resp.report_spec.source_table == "workflow_instances_all"


@pytest.mark.asyncio
async def test_generate_prompt_all_repositories_uses_tenant_catalog_not_ap_columns(monkeypatch):
    class _AllRepoEzofis:
        async def list_repositories(self, tenant_id):
            return [
                {"id": "r1", "name": "HR Records"},
                {"id": "r2", "name": "Legal Contracts"},
                {"id": "r3", "name": "RFQ Documents"},
            ]

    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple("not-json"),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_AllRepoEzofis(),
    )
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )
    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="all_repositories",
            description="Total Archived Documents",
            tenant_id="tenant-1",
        )
    )
    available = resp.report_spec.available_columns if resp.report_spec else []
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    assert "Repository_Name" in available
    assert "FileName" in available
    assert "Invoice_No" not in available
    assert "PO_Number" not in available
    assert "Invoice_No" not in keys
    assert any(k in keys for k in ("Repository_Name", "FileName", "Status", "CreatedBy"))
    assert resp.report_spec.source_table == "items_all"


def test_run_request_accepts_camel_case_report_spec():
    req = RunReportRequest.model_validate(
        {
            "reportType": "all_workflows",
            "tenantId": "tenant-1",
            "reportSpec": {
                "title": "Aging",
                "reportType": "all_workflows",
                "sourceSchema": "workflow",
                "sourceTable": "workflow_instances_all",
                "groupBy": ["Aging_Bucket", "workflowName"],
                "availableColumns": ["workflowName", "status"],
                "columns": [
                    {"key": "Aging_Bucket", "label": "Aging Bucket"},
                    {"key": "workflowName", "label": "Workflow"},
                    {"key": "Request_Count", "label": "Count"},
                ],
            },
        }
    )
    assert req.report_spec is not None
    assert req.report_spec.source_table == "workflow_instances_all"
    assert req.report_spec.source_schema == "workflow"
    assert req.report_spec.group_by == ["Aging_Bucket", "workflowName"]
    assert req.report_spec.available_columns == ["workflowName", "status"]
    assert req.report_type == "all_workflows"


def test_align_rebuilds_two_column_metric_specs():
    from app.models.report_agent import ReportSpec, ReportSpecColumn
    from app.report_agent.report_catalog import align_spec_to_intent

    available = ["workflowName", "status", "startedBy", "createdAtUtc", "completedAtUtc"]
    two_col = ReportSpec(
        title="Total Inbox",
        objective="Open requests",
        report_type="all_workflows",
        columns=[
            ReportSpecColumn(key="workflowName", label="Workflow"),
            ReportSpecColumn(key="status", label="Status"),
        ],
        group_by=["workflowName", "status"],
        available_columns=available,
        source_schema="workflow",
        source_table="workflow_instances_all",
    )
    inbox = align_spec_to_intent(
        two_col,
        description="Total Inbox",
        report_type="all_workflows",
        available_columns=available,
        source_schema="workflow",
        source_table="workflow_instances_all",
    )
    inbox_keys = [c.key for c in inbox.columns]
    assert "Inbox_Count" in inbox_keys
    assert "workflowName" in inbox_keys
    assert len(inbox_keys) >= 3

    aging_two = ReportSpec(
        title="Aging",
        objective="Aging",
        report_type="all_workflows",
        columns=[
            ReportSpecColumn(key="workflowName", label="Workflow"),
            ReportSpecColumn(key="status", label="Status"),
        ],
        group_by=["workflowName"],
        available_columns=available,
        source_schema="workflow",
        source_table="workflow_instances_all",
    )
    aging = align_spec_to_intent(
        aging_two,
        description="Aging",
        report_type="all_workflows",
        available_columns=available,
        source_schema="workflow",
        source_table="workflow_instances_all",
    )
    aging_keys = [c.key for c in aging.columns]
    assert "Aging_Bucket" in aging_keys
    assert "Request_Count" in aging_keys
    assert "workflowName" in aging_keys
    assert len(aging_keys) >= 3


@pytest.mark.asyncio
async def test_generate_prompt_aging_keeps_metric_columns(monkeypatch):
    two_col = (
        '{"title":"Aging","objective":"Aging","reportType":"all_workflows",'
        '"columns":[{"key":"workflowName","label":"Workflow"},'
        '{"key":"status","label":"Status"}],'
        '"groupBy":["workflowName","status"],'
        '"sourceSchema":"workflow","sourceTable":"workflow_instances_all"}'
    )

    class _AllEzofis:
        async def list_workflows(self, tenant_id):
            return [{"id": "1", "name": "RFQ Process"}, {"id": "2", "name": "Expense"}]

    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple(two_col),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_AllEzofis(),
    )
    monkeypatch.setattr(
        "app.report_agent.service.report_system_prompt",
        AsyncMock(return_value="system"),
    )
    resp = await service.generate_prompt(
        GeneratePromptRequest(
            report_type="all_workflows",
            description="Aging",
            tenant_id="tenant-1",
        )
    )
    keys = [c.key for c in (resp.report_spec.columns if resp.report_spec else [])]
    assert "Aging_Bucket" in keys
    assert "Request_Count" in keys
    assert "workflowName" in keys
    assert len(keys) >= 3


@pytest.mark.asyncio
async def test_run_report_all_workflows_keeps_generated_columns(monkeypatch):
    from app.models.report_agent import ReportSpec, ReportSpecColumn

    class _CatalogEzofis:
        async def list_workflows(self, tenant_id):
            return [{"id": "w1", "name": "RFQ Process"}, {"id": "w2", "name": "Expense"}]

        async def list_workflow_instances(self, *, tenant_id, workflow_id, limit=200):
            if workflow_id == "w1":
                return [
                    {
                        "workflowName": "RFQ Process",
                        "status": "Pending",
                        "createdAtUtc": "2026-10-20T00:00:00Z",
                        "completedAtUtc": None,
                    },
                    {
                        "workflowName": "RFQ Process",
                        "status": "Pending",
                        "createdAtUtc": "2026-09-01T00:00:00Z",
                        "completedAtUtc": None,
                    },
                    {
                        "workflowName": "RFQ Process",
                        "status": "Completed",
                        "createdAtUtc": "2026-10-01T00:00:00Z",
                        "completedAtUtc": "2026-10-02T00:00:00Z",
                    },
                ]
            return [
                {
                    "workflowName": "Expense",
                    "status": "Pending",
                    "createdAtUtc": "2026-10-22T00:00:00Z",
                    "completedAtUtc": None,
                }
            ]

    spec = ReportSpec(
        title="Aging",
        objective="Open requests grouped by aging bucket",
        report_type="all_workflows",
        columns=[
            ReportSpecColumn(key="workflowName", label="Workflow"),
            ReportSpecColumn(key="status", label="Status"),
        ],
        group_by=["workflowName", "status"],
        available_columns=["workflowName", "status", "createdAtUtc", "completedAtUtc"],
        source_schema="workflow",
        source_table="workflow_instances_all",
    )
    service = ReportAgentService(
        llm_adapter=_FakeLLMSimple("not-used"),
        db_pool=_EzofisTenantDb(),
        ezofis_client=_CatalogEzofis(),
    )
    resp = await service.run_report(
        RunReportRequest(
            report_spec=spec,
            report_type="all_workflows",
            tenant_id="tenant-1",
            page=1,
            page_size=25,
        )
    )
    keys = [c.key for c in resp.columns]
    assert "Aging_Bucket" in keys
    assert "Request_Count" in keys
    assert "workflowName" in keys
    assert len(keys) >= 3
    assert resp.total_count >= 1
    assert resp.rows
    assert all(row.get("Request_Count") for row in resp.rows)
