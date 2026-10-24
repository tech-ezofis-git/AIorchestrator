"""Scope live schema slices and name pickers for Report Types."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice, ScopeOption
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.report_types import ReportTypeDefinition

_WORKFLOW_PLUMBING_PREFIXES = (
    "workflow_attachments_",
    "workflow_signatures_",
    "workflow_emails_",
    "workflow_notifications_",
    "workflow_activity_",
    "workflow_audit_",
    "workflow_history_",
    "workflow_pdf_annotations_",
    "workflow_ai_validations_",
    "ap_agent_",
    "apagent",
)

_ORCH_SKIP = frozenset(
    {
        "ap_runs",
        "ap_skill_artifacts",
        "ap_tenant_plans",
        "ap_credit_ledger",
        "documents",
        "chunks",
    }
)

_CATALOG_TABLES = frozenset(
    {
        "wworkflow",
        "wworkflows",
        "workflows",
        "workflowdefinitions",
        "wrepository",
        "wrepositories",
        "repositories",
        "repositorydefinitions",
        "wform",
        "wforms",
        "forms",
        "formdefinitions",
        "ezfb_forms",
    }
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _qualified(schema: str, table: str) -> str:
    if schema and schema.lower() not in ("", "public"):
        return f"{schema}.{table}"
    return table


def _is_plumbing(table: str) -> bool:
    t = table.lower()
    if t in _ORCH_SKIP:
        return True
    return any(t.startswith(p) for p in _WORKFLOW_PLUMBING_PREFIXES)


def is_catalog_table(table: str) -> bool:
    return (table or "").lower() in _CATALOG_TABLES


def is_fact_table(table: str) -> bool:
    t = (table or "").lower()
    if not t or is_catalog_table(t):
        return False
    return (
        (t.startswith("ezfb_") and "_items" in t)
        or t.startswith("items_")
        or "items_" in t
        or t.startswith("inbox_")
        or t.startswith("workflow_instances_")
        or t.startswith("workflow_tasks_")
    )


def preferred_fact_table(tables: list[SchemaTableSlice]) -> Optional[SchemaTableSlice]:
    for table in tables:
        if is_fact_table(table.table):
            return table
    return tables[0] if tables else None


def _table_matches_focus(schema: str, table: str, focus: tuple[str, ...]) -> bool:
    blob = f"{schema}.{table}".lower()
    return any(tok.lower() in blob for tok in focus)


def _workflow_match(schema_obj: DatabaseSchema, table: str, workflow_name: str) -> bool:
    want = _norm(workflow_name)
    if not want:
        return True
    mapped = schema_obj.get_workflow_for_table(table)
    if mapped and _norm(mapped) == want:
        return True
    # also allow tables whose name embeds workflow id tokens mapped to that name
    for key, name in schema_obj.table_to_workflow.items():
        if _norm(name) != want:
            continue
        if _norm(key) and _norm(key) in _norm(table):
            return True
    return False


def _repo_match(schema_obj: DatabaseSchema, table: str, repository_name: str) -> bool:
    want = _norm(repository_name)
    if not want:
        return True
    for key, name in schema_obj.table_to_workflow.items():
        # repository names are also stored in table_to_workflow via repo meta discovery
        if _norm(name) != want:
            continue
        if _norm(key) and _norm(key) in _norm(table):
            return True
    # fallback: items_* tables always candidate when specific repo; SQL filters apply later
    return table.lower().startswith("items_") or "items_" in table.lower()


def scope_schema(
    schema_obj: DatabaseSchema,
    report_type: ReportTypeDefinition,
    *,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
    max_tables: int = 18,
    max_columns_per_table: int = 24,
) -> list[SchemaTableSlice]:
    """Return an approved schema slice for the LLM (never invent).

    Defaults stay intentionally small so Phase 1/2 prompts fit within
    typical provider timeouts (large tenant schemas were timing out).
    Catalog definition tables (Workflows, Repositories, wForm) are omitted
    when a real items/form table exists — those catalogs have no report rows.
    """
    candidates: list[tuple[str, str, list[ColumnMeta]]] = []
    for schema_name, table in schema_obj.tables:
        if _is_plumbing(table):
            continue
        if report_type.schema_focus and not _table_matches_focus(schema_name, table, report_type.schema_focus):
            # keep definition tables even if focus is narrow
            if table.lower() not in (
                "wworkflow",
                "wworkflows",
                "workflows",
                "wrepository",
                "wrepositories",
                "repositories",
            ) and not is_fact_table(table):
                continue
        if report_type.key == "specific_workflow" and workflow_name:
            if is_catalog_table(table) and table.lower() not in ("wworkflow", "wworkflows", "workflows"):
                continue
            if not is_catalog_table(table) and not _workflow_match(schema_obj, table, workflow_name):
                continue
        if report_type.key == "specific_repository" and repository_name:
            if is_catalog_table(table) and table.lower() not in ("wrepository", "wrepositories", "repositories"):
                continue
            if not is_catalog_table(table) and not _repo_match(schema_obj, table, repository_name):
                continue
        cols = schema_obj.get_columns_for_table(schema_name, table)
        if not cols:
            continue
        candidates.append((schema_name, table, cols))

    facts = [item for item in candidates if is_fact_table(item[1])]
    if not facts and report_type.key in ("all_workflows", "all_repositories"):
        for schema_name, table in schema_obj.tables:
            if _is_plumbing(table) or not is_fact_table(table):
                continue
            cols = schema_obj.get_columns_for_table(schema_name, table)
            if cols:
                facts.append((schema_name, table, cols))
    if facts:
        candidates = facts

    def _rank(item: tuple[str, str, list[ColumnMeta]]) -> tuple[int, int, str]:
        s, t, _ = item
        sl = s.lower()
        fact = 0 if is_fact_table(t) else (2 if is_catalog_table(t) else 1)
        schema_order = 0 if sl in ("dbo", "workflow", "repository") else 1
        return (fact, schema_order, t.lower())

    candidates.sort(key=_rank)
    out: list[SchemaTableSlice] = []
    for schema_name, table, cols in candidates[:max_tables]:
        col_limit = 80 if is_fact_table(table) else max_columns_per_table
        out.append(
            SchemaTableSlice(
                schema_name=schema_name,
                table=table,
                columns=[
                    SchemaColumnSlice(name=c.column, type=c.data_type)
                    for c in cols[:col_limit]
                ],
            )
        )
    return out


def schema_slice_to_prompt_block(tables: list[SchemaTableSlice]) -> str:
    lines: list[str] = [
        "Approved live schema slice (use ONLY these tables/columns):",
        "These are report row sources. Do not query catalog definition tables "
        "(Workflows, Repositories, wForm) for report rows.",
    ]
    preferred = preferred_fact_table(tables)
    if preferred:
        lines.append(
            "Preferred source table: "
            f"{_qualified(preferred.schema_name or '', preferred.table)}"
        )
    if not tables:
        lines.append("(no matching tables discovered)")
        return "\n".join(lines)
    for t in tables:
        q = _qualified(t.schema_name or "", t.table)
        col_bits = ", ".join(f"{c.name}:{c.type}" for c in t.columns)
        lines.append(f"- {q}: {col_bits}")
    return "\n".join(lines)


def list_workflow_options(schema_obj: DatabaseSchema) -> list[ScopeOption]:
    options: list[ScopeOption] = []
    seen: set[str] = set()
    for wf in schema_obj.workflows:
        name = (wf.name or "").strip()
        wid = (wf.workflow_id or "").strip()
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        options.append(ScopeOption(id=wid or name, name=name))
    if options:
        options.sort(key=lambda o: o.name.lower())
        return options
    for key, name in schema_obj.table_to_workflow.items():
        n = (name or "").strip()
        if not n or _norm(n) in seen:
            continue
        seen.add(_norm(n))
        options.append(ScopeOption(id=key, name=n))
    options.sort(key=lambda o: o.name.lower())
    return options


def list_repository_options(schema_obj: DatabaseSchema) -> list[ScopeOption]:
    options: list[ScopeOption] = []
    seen: set[str] = set()
    for repo in getattr(schema_obj, "repositories", None) or []:
        n = (repo.name or "").strip()
        if not n or _norm(n) in seen:
            continue
        seen.add(_norm(n))
        options.append(ScopeOption(id=repo.repository_id or n, name=n))
    if options:
        options.sort(key=lambda o: o.name.lower())
        return options
    for key, name in schema_obj.table_to_workflow.items():
        n = (name or "").strip()
        if not n or _norm(n) in seen:
            continue
        if any(
            t.lower().startswith("items_") and _norm(key) in _norm(t)
            for _, t in schema_obj.tables
        ):
            seen.add(_norm(n))
            options.append(ScopeOption(id=key, name=n))
    options.sort(key=lambda o: o.name.lower())
    return options


def resolve_scope_options(schema_obj: DatabaseSchema, scope: str) -> list[ScopeOption]:
    scope_l = (scope or "").strip().lower()
    if scope_l in ("workflows", "workflow"):
        return list_workflow_options(schema_obj)
    if scope_l in ("repositories", "repository"):
        return list_repository_options(schema_obj)
    raise ValueError("scope must be 'workflows' or 'repositories'.")
