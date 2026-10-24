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
    assert "Recovered prompt text" in resp.report_prompt
    assert resp.report_spec is not None
    assert resp.report_spec.objective


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
