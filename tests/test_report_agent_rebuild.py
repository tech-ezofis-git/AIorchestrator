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
        return {"model": "primary-model"}, {"model": "fallback-model"}

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
    assert "inbox_aaaaaaaa" in resp.report_prompt or "Report objective" in resp.report_prompt
    assert resp.report_type == "all_workflows"


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
