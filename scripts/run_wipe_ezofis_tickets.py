#!/usr/bin/env python3
"""Stand-alone wipe for EZOFIS workflow tickets on postgrev6southinddb.

Prompts for DB password (does not use stale .env password unless you pass it).

One workflow (AP Agent or any other). Preview, then execute:
  py -3 scripts/run_wipe_ezofis_tickets.py --tenant-id <tenant-guid> --workflow-id <workflow-guid>
  py -3 scripts/run_wipe_ezofis_tickets.py --tenant-id <tenant-guid> --workflow-id <workflow-guid> --execute

Whole AP tenant (original wipe, every workflow table):
  py -3 scripts/run_wipe_ezofis_tickets.py
  py -3 scripts/run_wipe_ezofis_tickets.py --execute

Optional env:
  PGUSER=postgrev6southindadmin
  PGPASSWORD=<your password>
  PGHOST=postgrev6southinddb.postgres.database.azure.com
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TENANT = "b843b988-00ec-44e3-aca2-b8470133ef63"
DEFAULT_HOST = "postgrev6southinddb.postgres.database.azure.com"
DEFAULT_USER = "v6dbadmin"
CATALOG_DB = "ezofis_catalog_new"
AP_TENANT_DB = "ezofis_Tenant_b843b988"
# Accounts Payable form / repo (from live tickets)
REPO_ITEMS = "items_a6169a5c"
EZFB_ITEMS = "ezfb_6e45749f_items"

WORKFLOW_WHERE = """
table_schema = 'workflow'
AND table_type = 'BASE TABLE'
AND (
      table_name IN (
        'WorkflowInstanceLookup',
        'WorkflowApprovals',
        'ApAgentJobProgress',
        'jiraCreateIssue'
      )
   OR table_name LIKE 'workflow_instances_%'
   OR table_name LIKE 'workflow_step_instances_%'
   OR table_name LIKE 'workflow_instance_slas_%'
   OR table_name LIKE 'workflow_instance_user_state_%'
   OR table_name LIKE 'transaction_%'
   OR table_name LIKE 'process_form_%'
   OR table_name LIKE 'process_addon_%'
   OR table_name LIKE 'inbox_%'
   OR table_name LIKE 'sent_%'
   OR table_name LIKE 'completed_%'
   OR table_name LIKE 'workflow_comments_%'
   OR table_name LIKE 'workflow_attachments_%'
   OR table_name LIKE 'workflow_forms_%'
   OR table_name LIKE 'workflow_tasks_%'
   OR table_name LIKE 'workflow_signatures_%'
   OR table_name LIKE 'workflow_documents_%'
   OR table_name LIKE 'workflow_emails_%'
   OR table_name LIKE 'workflow_ai_validations_%'
   OR table_name LIKE 'agent_data_validation_%'
   OR table_name LIKE 'workflow_pdf_annotations_%'
   OR table_name LIKE 'workflow_notifications_%'
   OR table_name LIKE 'workflow_activity_%'
   OR table_name LIKE 'workflow_audit_%'
   OR table_name LIKE 'workflow_history_%'
   OR table_name LIKE 'workflow_request_%'
   OR table_name LIKE 'ap_agent_%'
   OR table_name LIKE 'ApAgent%'
)
"""


def rewrite_host(text: str, host: str) -> str:
    return (text or "").replace("ezv6psql.postgres.database.azure.com", host)


async def connect(host: str, user: str, password: str, database: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=host,
        port=5432,
        user=user,
        password=password,
        database=database,
        ssl="require",
        timeout=60,
    )


async def table_exists(conn: asyncpg.Connection, schema: str, name: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = $1 AND table_name = $2 AND table_type = 'BASE TABLE'
        """,
        schema,
        name,
    )
    return bool(row)


def compact_guid(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch in "0123456789abcdef")


def workflow_suffixes(workflow_id: str) -> tuple[str, str]:
    """Per-workflow tables use the first 8 hex chars, sometimes the full guid."""
    compact = compact_guid(workflow_id)
    if len(compact) < 8:
        raise SystemExit(f"workflow id is not a guid: {workflow_id!r}")
    return compact[:8], compact


def table_matches_workflow(table_name: str, short: str, full: str) -> bool:
    name = (table_name or "").lower()
    return name.endswith("_" + short) or name.endswith("_" + full)


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def qident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise SystemExit(f"refusing unsafe SQL identifier: {name!r}")
    return '"' + name + '"'


async def list_wipe_tables(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    rows = await conn.fetch(
        f"""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE {WORKFLOW_WHERE}
        ORDER BY table_name
        """
    )
    return [(r["table_schema"], r["table_name"]) for r in rows]


async def truncate(conn: asyncpg.Connection, schema: str, name: str) -> None:
    await conn.execute(f'TRUNCATE TABLE "{schema}"."{name}" RESTART IDENTITY CASCADE')
    print(f"  truncated {schema}.{name}")


async def resolve_app_database(
    catalog: asyncpg.Connection, host: str, tenant_id: str, fallback_db: str
) -> str:
    """Prefer catalog.Tenants.ConnectionString database; fallback when the catalog row is missing."""
    row = await catalog.fetchrow(
        """
        SELECT "ConnectionString" AS cs
        FROM catalog."Tenants"
        WHERE replace(lower("Id"::text), '-', '') = replace(lower($1::text), '-', '')
        LIMIT 1
        """,
        tenant_id,
    )
    if not row or not row["cs"]:
        print(f"  WARN: no ConnectionString; using {fallback_db}")
        return fallback_db
    cs = rewrite_host(str(row["cs"]), host)
    # ADO.NET: Database=... or Initial Catalog=...
    lower = cs.lower()
    for key in ("database=", "initial catalog="):
        if key in lower:
            start = lower.index(key) + len(key)
            end = cs.find(";", start)
            db = cs[start:] if end < 0 else cs[start:end]
            db = db.strip().strip('"')
            if db:
                print(f"  tenant app DB from ConnectionString: {db}")
                return db
    # URI form
    if "://" in cs and "/" in cs.split("@")[-1]:
        db = cs.split("@")[-1].split("/", 1)[-1].split("?")[0]
        if db:
            print(f"  tenant app DB from URI: {db}")
            return db
    print(f"  WARN: could not parse ConnectionString; using {fallback_db}")
    return fallback_db


async def list_workflow_tables(
    conn: asyncpg.Connection, workflow_id: str
) -> list[tuple[str, str]]:
    short, full = workflow_suffixes(workflow_id)
    tables = await list_wipe_tables(conn)
    return [(schema, name) for schema, name in tables if table_matches_workflow(name, short, full)]


async def _column_map(conn: asyncpg.Connection, schema: str, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    return {str(row["column_name"]).lower(): str(row["column_name"]) for row in rows}


def _pick_column(columns: dict[str, str], *names: str) -> str | None:
    for name in names:
        found = columns.get(name.lower())
        if found:
            return found
    return None


async def _workflow_id_column(conn: asyncpg.Connection, schema: str, name: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
          AND lower(column_name) IN ('workflowid', 'workflow_id', 'wworkflowid')
        ORDER BY column_name
        LIMIT 1
        """,
        schema,
        name,
    )
    return str(row["column_name"]) if row else None


def _split_items_table(raw: str, repository_id: str) -> tuple[str, str]:
    text = (raw or "").strip().strip('"')
    if "." in text:
        schema, table = text.split(".", 1)
        schema = schema.strip().strip('"')
        table = table.strip().strip('"')
    else:
        schema, table = "repository", text
    if not table:
        compact = compact_guid(repository_id)
        table = f"items_{compact[:8]}" if len(compact) >= 8 else ""
    return schema or "repository", table


async def _locate_table(conn: asyncpg.Connection, table_name: str) -> tuple[str, str] | None:
    if not table_name:
        return None
    row = await conn.fetchrow(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE lower(table_name) = lower($1)
          AND table_type = 'BASE TABLE'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE
            WHEN table_schema = 'repository' THEN 0
            WHEN table_schema = 'dbo' THEN 1
            WHEN table_schema = 'public' THEN 2
            ELSE 3
        END
        LIMIT 1
        """,
        table_name,
    )
    if row is None:
        return None
    return str(row["table_schema"]), str(row["table_name"])


def _ezfb_items_name(form_id: str) -> str:
    raw = (form_id or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return f"ezfb_{int(raw)}_items"
    compact = compact_guid(raw)
    if len(compact) >= 8:
        return f"ezfb_{compact[:8]}_items"
    return ""


async def _item_family(conn: asyncpg.Connection, schema: str, table: str) -> list[tuple[str, str]]:
    """History and stage rows first, then the items table itself."""
    found: list[tuple[str, str]] = []
    for suffix in ("_history", "_stage", "_versions", "_files"):
        name = f"{table}{suffix}"
        if await table_exists(conn, schema, name):
            found.append((schema, name))
    if await table_exists(conn, schema, table):
        found.append((schema, table))
    return found


async def resolve_workflow_repository(conn: asyncpg.Connection, workflow_id: str) -> dict | None:
    """workflow.Workflows.RepositoryId → repository.Repositories items table."""
    if not await table_exists(conn, "workflow", "Workflows"):
        print("  workflow.Workflows missing; repository files not resolved")
        return None
    wf_cols = await _column_map(conn, "workflow", "Workflows")
    id_col = _pick_column(wf_cols, "id")
    repo_col = _pick_column(wf_cols, "repositoryid", "repository_id")
    form_col = _pick_column(wf_cols, "formid", "form_id", "wformid")
    name_col = _pick_column(wf_cols, "name")
    if not id_col or not repo_col:
        print("  workflow.Workflows has no repository id; repository files not resolved")
        return None
    select = [
        f"{qident(id_col)}::text AS id",
        f"{qident(repo_col)}::text AS repository_id",
    ]
    if form_col:
        select.append(f"{qident(form_col)}::text AS form_id")
    if name_col:
        select.append(f"{qident(name_col)}::text AS name")
    deleted = _pick_column(wf_cols, "isdeleted", "is_deleted")
    row = await conn.fetchrow(
        f"SELECT {', '.join(select)} FROM workflow.{qident('Workflows')} "
        f"WHERE replace(lower({qident(id_col)}::text), '-', '') = $1 LIMIT 1",
        compact_guid(workflow_id),
    )
    if row is None or not row["repository_id"]:
        print("  this workflow has no repository linked")
        return None
    repository_id = str(row["repository_id"])
    repository_compact = compact_guid(repository_id)
    workflow_name = str(row["name"]) if name_col and row["name"] else ""

    other_workflows = 0
    if repository_compact:
        deleted_sql = ""
        if deleted:
            deleted_sql = (
                f" AND lower(COALESCE({qident(deleted)}::text, 'false'))"
                " NOT IN ('1', 'true', 't')"
            )
        other_workflows = int(
            await conn.fetchval(
                f"SELECT count(*) FROM workflow.{qident('Workflows')} "
                f"WHERE replace(lower({qident(repo_col)}::text), '-', '') = $1 "
                f"AND replace(lower({qident(id_col)}::text), '-', '') <> $2"
                f"{deleted_sql}",
                repository_compact,
                compact_guid(workflow_id),
            )
            or 0
        )

    schema, table = "repository", f"items_{repository_compact[:8]}" if len(repository_compact) >= 8 else ""
    repository_name = ""
    if await table_exists(conn, "repository", "Repositories"):
        repo_cols = await _column_map(conn, "repository", "Repositories")
        repo_id_col = _pick_column(repo_cols, "id")
        items_col = _pick_column(repo_cols, "itemstablename", "items_table_name")
        repo_name_col = _pick_column(repo_cols, "name")
        if repo_id_col:
            repo_select = [f"{qident(repo_id_col)}::text AS id"]
            if items_col:
                repo_select.append(f"{qident(items_col)}::text AS items_table")
            if repo_name_col:
                repo_select.append(f"{qident(repo_name_col)}::text AS name")
            repo_row = await conn.fetchrow(
                f"SELECT {', '.join(repo_select)} FROM repository.{qident('Repositories')} "
                f"WHERE replace(lower({qident(repo_id_col)}::text), '-', '') = $1 LIMIT 1",
                repository_compact,
            )
            if repo_row is not None:
                repository_name = str(repo_row["name"] or "") if repo_name_col else ""
                if items_col and repo_row["items_table"]:
                    schema, table = _split_items_table(str(repo_row["items_table"]), repository_id)
    located = await _locate_table(conn, table) if table else None
    if located is None and len(repository_compact) >= 8:
        located = await _locate_table(conn, f"items_{repository_compact[:8]}")
    if located is None:
        schema, table = "", ""
    else:
        schema, table = located

    form_id = str(row["form_id"] or "") if form_col and row["form_id"] else ""
    form_schema, form_table = "", ""
    other_form_workflows = 0
    form_name = _ezfb_items_name(form_id)
    if form_name:
        form_located = await _locate_table(conn, form_name)
        if form_located is not None:
            form_schema, form_table = form_located
            if form_col:
                deleted_sql = ""
                if deleted:
                    deleted_sql = (
                        f" AND lower(COALESCE({qident(deleted)}::text, 'false'))"
                        " NOT IN ('1', 'true', 't')"
                    )
                other_form_workflows = int(
                    await conn.fetchval(
                        f"SELECT count(*) FROM workflow.{qident('Workflows')} "
                        f"WHERE replace(lower({qident(form_col)}::text), '-', '') = $1 "
                        f"AND replace(lower({qident(id_col)}::text), '-', '') <> $2"
                        f"{deleted_sql}",
                        compact_guid(form_id) or form_id.lower(),
                        compact_guid(workflow_id),
                    )
                    or 0
                )
    return {
        "workflow_name": workflow_name,
        "repository_id": repository_id,
        "repository_name": repository_name,
        "repository_compact": repository_compact,
        "schema": schema,
        "table": table,
        "other_workflows": other_workflows,
        "form_id": form_id,
        "form_schema": form_schema,
        "form_table": form_table,
        "other_form_workflows": other_form_workflows,
    }


async def _collect_attachment_items(
    conn: asyncpg.Connection, workflow_id: str
) -> list[tuple[str, str]]:
    """Item ids still stored on this workflow's attachment/document tables."""
    short, full = workflow_suffixes(workflow_id)
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for prefix in ("workflow_attachments_", "workflow_documents_"):
        for suffix in (short, full):
            name = prefix + suffix
            if not await table_exists(conn, "workflow", name):
                continue
            columns = await _column_map(conn, "workflow", name)
            item_col = _pick_column(
                columns, "itemid", "item_id", "repositoryitemid", "ifileid", "witemid"
            )
            if not item_col:
                continue
            repo_col = _pick_column(columns, "repositoryid", "repository_id", "wrepositoryid")
            repo_sql = f"{qident(repo_col)}::text" if repo_col else "NULL::text"
            rows = await conn.fetch(
                f"SELECT {qident(item_col)}::text AS item_id, {repo_sql} AS repository_id "
                f"FROM workflow.{qident(name)} "
                f"WHERE {qident(item_col)} IS NOT NULL"
            )
            for row in rows:
                item = compact_guid(str(row["item_id"] or ""))
                repo = compact_guid(str(row["repository_id"] or ""))
                if not item or (item, repo) in seen:
                    continue
                seen.add((item, repo))
                found.append((item, repo))
    return found


async def _collect_instance_ids(conn: asyncpg.Connection, workflow_id: str) -> list[str]:
    short, full = workflow_suffixes(workflow_id)
    ids: list[str] = []
    seen: set[str] = set()
    for suffix in (short, full):
        for name in (f"workflow_instances_{suffix}", f"workflowinstances_{suffix}"):
            if not await table_exists(conn, "workflow", name):
                continue
            columns = await _column_map(conn, "workflow", name)
            id_col = _pick_column(columns, "id", "instanceid", "instance_id", "workflowinstanceid")
            if not id_col:
                continue
            rows = await conn.fetch(
                f"SELECT {qident(id_col)}::text AS id FROM workflow.{qident(name)} "
                f"WHERE {qident(id_col)} IS NOT NULL"
            )
            for row in rows:
                compact = compact_guid(str(row["id"] or ""))
                if compact and compact not in seen:
                    seen.add(compact)
                    ids.append(compact)
    return ids


def _repository_match_where(
    columns: dict[str, str],
    *,
    item_ids: list[str],
    instance_ids: list[str],
    workflow_compact: str,
) -> tuple[str, list]:
    clauses: list[str] = []
    args: list = []
    item_col = _pick_column(columns, "itemid", "item_id", "id", "ifileid", "repositoryitemid")
    if item_col and item_ids:
        args.append(item_ids)
        clauses.append(
            f"replace(lower({qident(item_col)}::text), '-', '') = ANY(${len(args)}::text[])"
        )
    workflow_col = _pick_column(columns, "workflowid", "workflow_id", "wworkflowid", "iworkflowid")
    if workflow_col and workflow_compact:
        args.append(workflow_compact)
        clauses.append(
            f"replace(lower({qident(workflow_col)}::text), '-', '') = ${len(args)}"
        )
    instance_col = _pick_column(
        columns,
        "workflow_instance_id",
        "workflowinstanceid",
        "instanceid",
        "instance_id",
        "winstanceid",
    )
    if instance_col and instance_ids:
        args.append(instance_ids)
        clauses.append(
            f"replace(lower({qident(instance_col)}::text), '-', '') = ANY(${len(args)}::text[])"
        )
    if not clauses:
        return "", []
    return " WHERE " + " OR ".join(clauses), args


async def repository_file_targets(conn: asyncpg.Connection, workflow_id: str) -> list[dict]:
    """Files to clear: the workflow's repository, plus attachment item ids."""
    link = await resolve_workflow_repository(conn, workflow_id)
    attachments = await _collect_attachment_items(conn, workflow_id)
    instance_ids = await _collect_instance_ids(conn, workflow_id)
    workflow_compact = compact_guid(workflow_id)
    targets: list[dict] = []
    covered: set[tuple[str, str]] = set()

    def add_family(schema: str, table: str, mode: str, item_ids: list[str]) -> None:
        # Family is resolved by the caller; this only records one table.
        key = (schema, table.lower())
        if key in covered or not table:
            return
        covered.add(key)
        targets.append(
            {
                "schema": schema,
                "table": table,
                "mode": mode,
                "item_ids": item_ids,
                "instance_ids": instance_ids,
                "workflow_compact": workflow_compact,
                "link": link,
            }
        )

    if link and link.get("table"):
        # AP invoice files are the rows in the workflow's repository. Ticket
        # tables do not keep a per-file key once they are truncated, so the
        # whole linked library is cleared.
        family = await _item_family(conn, link["schema"], link["table"])
        for schema, table in family:
            add_family(schema, table, "all", [])
    if link and link.get("form_table") and int(link.get("other_form_workflows") or 0) == 0:
        for schema, table in await _item_family(conn, link["form_schema"], link["form_table"]):
            add_family(schema, table, "all", [])

    extra_repos = {
        repo
        for _item, repo in attachments
        if repo and (not link or repo != link.get("repository_compact"))
    }
    for repo in extra_repos:
        table = f"items_{repo[:8]}"
        if not await table_exists(conn, "repository", table):
            continue
        item_ids = [item for item, item_repo in attachments if item_repo == repo]
        for schema, name in await _item_family(conn, "repository", table):
            add_family(schema, name, "match", item_ids)

    if not link and attachments:
        rows = await conn.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = 'repository'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'items_%'
              AND table_name NOT LIKE '%\\_stage' ESCAPE '\\'
              AND table_name NOT LIKE '%\\_history' ESCAPE '\\'
              AND table_name NOT LIKE '%\\_versions' ESCAPE '\\'
              AND table_name NOT LIKE '%\\_files' ESCAPE '\\'
            """
        )
        item_ids = [item for item, _repo in attachments]
        for row in rows:
            for schema, name in await _item_family(conn, row["table_schema"], row["table_name"]):
                add_family(schema, name, "match", item_ids)
    return targets


async def _repository_target_count(conn: asyncpg.Connection, target: dict) -> int | None:
    schema, table = target["schema"], target["table"]
    qualified = f"{qident(schema)}.{qident(table)}"
    if target["mode"] == "all":
        return int(await conn.fetchval(f"SELECT count(*) FROM {qualified}") or 0)
    columns = await _column_map(conn, schema, table)
    where, args = _repository_match_where(
        columns,
        item_ids=target["item_ids"],
        instance_ids=target["instance_ids"],
        workflow_compact=target["workflow_compact"],
    )
    if not where:
        return None
    return int(await conn.fetchval(f"SELECT count(*) FROM {qualified}{where}", *args) or 0)


async def preview_repository_files(conn: asyncpg.Connection, workflow_id: str) -> list[dict]:
    targets = await repository_file_targets(conn, workflow_id)
    print("\n=== Repository files linked to this workflow ===")
    if not targets:
        print("  no repository items table linked to this workflow")
        return targets
    link = targets[0].get("link") or {}
    if link:
        label = link.get("repository_name") or link.get("repository_id") or ""
        others = int(link.get("other_workflows") or 0)
        scope = "all files in this workflow's repository will be deleted"
        if others:
            scope = (
                f"WARNING: {others} other workflow(s) use this repository. "
                "All files in it will still be deleted."
            )
        print(f"  repository {label} ({link.get('repository_id')})")
        if link.get("workflow_name"):
            print(f"  workflow {link['workflow_name']}")
        print(f"  {scope}")
        if link.get("form_table"):
            form_note = f"  form {link['form_schema']}.{link['form_table']}"
            if int(link.get("other_form_workflows") or 0) == 0:
                form_note += " — all rows will be deleted"
            else:
                form_note += " — left in place (shared with another workflow)"
            print(form_note)
    for target in targets:
        count = await _repository_target_count(conn, target)
        name = f"{target['schema']}.{target['table']}"
        if count is None:
            print(f"  {name} left untouched (no workflow, instance, or item link on this table)")
        else:
            print(f"  {name} rows={count}")
    return targets


async def delete_repository_files(conn: asyncpg.Connection, workflow_id: str) -> None:
    targets = await repository_file_targets(conn, workflow_id)
    if not targets:
        print("  no repository files linked to this workflow")
        return
    print("  clearing repository files")
    for target in targets:
        schema, table = target["schema"], target["table"]
        qualified = f"{qident(schema)}.{qident(table)}"
        if target["mode"] == "all":
            result = await conn.execute(f"DELETE FROM {qualified}")
            print(f"  {result} {schema}.{table}")
            continue
        columns = await _column_map(conn, schema, table)
        where, args = _repository_match_where(
            columns,
            item_ids=target["item_ids"],
            instance_ids=target["instance_ids"],
            workflow_compact=target["workflow_compact"],
        )
        if not where:
            print(f"  skip {schema}.{table} (shared repository, no remaining file link)")
            continue
        result = await conn.execute(f"DELETE FROM {qualified}{where}", *args)
        print(f"  {result} {schema}.{table}")


_AP_HISTORY_TABLES = ("ap_skill_artifacts", "ap_runs", "ap_credit_ledger")


async def preview_ap_history(conn: asyncpg.Connection) -> None:
    """Prior MATCHED rows here make the next invoice with the same number a duplicate."""
    print("\n=== AP run history (duplicate check) ===")
    for name in _AP_HISTORY_TABLES:
        if not await table_exists(conn, "public", name):
            print(f"  public.{name} missing")
            continue
        count = await conn.fetchval(f"SELECT count(*) FROM public.{qident(name)}")
        print(f"  public.{name} rows={count}")


async def delete_ap_history(conn: asyncpg.Connection) -> None:
    print("  clearing AP run history")
    for name in _AP_HISTORY_TABLES:
        if not await table_exists(conn, "public", name):
            print(f"  skip public.{name} (missing)")
            continue
        await truncate(conn, "public", name)


async def preview_one_workflow(conn: asyncpg.Connection, workflow_id: str) -> list[tuple[str, str]]:
    db = await conn.fetchval("SELECT current_database()")
    short, _full = workflow_suffixes(workflow_id)
    print(f"\n=== PREVIEW workflow {workflow_id} (suffix {short}) on {db} ===")
    tables = await list_workflow_tables(conn, workflow_id)
    if not tables:
        print("  no per-workflow tables matched this workflow id")
    for schema, name in tables:
        count = await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{name}"')
        print(f"  {schema}.{name} rows={count}")
    for name in ("WorkflowInstanceLookup", "WorkflowApprovals", "ApAgentJobProgress", "jiraCreateIssue"):
        if not await table_exists(conn, "workflow", name):
            continue
        col = await _workflow_id_column(conn, "workflow", name)
        if not col:
            print(f"  workflow.{name} has no workflow id column; left untouched")
            continue
        count = await conn.fetchval(
            f'SELECT count(*) FROM workflow."{name}" '
            f"""WHERE replace(lower("{col}"::text), '-', '') = $1""",
            compact_guid(workflow_id),
        )
        print(f"  workflow.{name}.{col} matching rows={count}")
    await preview_repository_files(conn, workflow_id)
    await preview_ap_history(conn)
    return tables


async def wipe_one_workflow(conn: asyncpg.Connection, workflow_id: str) -> None:
    db = await conn.fetchval("SELECT current_database()")
    tables = await list_workflow_tables(conn, workflow_id)
    repo_targets = await repository_file_targets(conn, workflow_id)
    if not tables and not repo_targets:
        raise SystemExit(
            f"No workflow tables or repository files matched {workflow_id} on {db}. Nothing deleted."
        )
    print(f"\n=== Wiping workflow {workflow_id} on {db} ===")
    compact = compact_guid(workflow_id)
    async with conn.transaction():
        await delete_repository_files(conn, workflow_id)
        await delete_ap_history(conn)
        for schema, name in tables:
            await truncate(conn, schema, name)
        for name in ("WorkflowInstanceLookup", "WorkflowApprovals", "ApAgentJobProgress", "jiraCreateIssue"):
            if not await table_exists(conn, "workflow", name):
                continue
            col = await _workflow_id_column(conn, "workflow", name)
            if not col:
                continue
            result = await conn.execute(
                f'DELETE FROM workflow."{name}" '
                f"""WHERE replace(lower("{col}"::text), '-', '') = $1""",
                compact,
            )
            print(f"  {result} workflow.{name}")


async def wipe_app_db(conn: asyncpg.Connection) -> None:
    db = await conn.fetchval("SELECT current_database()")
    print(f"\n=== Wiping APP DB: {db} ===")
    tables = await list_wipe_tables(conn)
    print(f"workflow targets: {len(tables)}")
    async with conn.transaction():
        for schema, name in tables:
            await truncate(conn, schema, name)

        if await table_exists(conn, "workflow", "WorkflowDocuments"):
            result = await conn.execute(
                """
                DELETE FROM workflow."WorkflowDocuments"
                WHERE "WorkflowInstanceId" IS NOT NULL
                """
            )
            print(f"  {result} workflow.WorkflowDocuments")

        item_tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'repository'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'items_%'
              AND table_name NOT LIKE '%\\_stage' ESCAPE '\\'
              AND table_name NOT LIKE '%\\_history' ESCAPE '\\'
            """
        )
        for r in item_tables:
            tname = r["table_name"]
            has_col = await conn.fetchrow(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'repository'
                  AND table_name = $1
                  AND lower(column_name) = 'workflow_instance_id'
                """,
                tname,
            )
            if has_col:
                await conn.execute(
                    f'UPDATE repository."{tname}" '
                    f"SET workflow_instance_id = NULL "
                    f"WHERE workflow_instance_id IS NOT NULL"
                )
                print(f"  unlinked workflow_instance_id on repository.{tname}")

        if await table_exists(conn, "repository", REPO_ITEMS):
            await truncate(conn, "repository", REPO_ITEMS)

        if await table_exists(conn, "dbo", EZFB_ITEMS):
            await truncate(conn, "dbo", EZFB_ITEMS)
        elif await table_exists(conn, "public", EZFB_ITEMS):
            await truncate(conn, "public", EZFB_ITEMS)

        for name in ("ap_credit_ledger", "ap_skill_artifacts", "ap_runs"):
            if await table_exists(conn, "public", name):
                await truncate(conn, "public", name)


async def wipe_ap_tenant_db(conn: asyncpg.Connection) -> None:
    db = await conn.fetchval("SELECT current_database()")
    print(f"\n=== Wiping AP TENANT DB: {db} ===")
    async with conn.transaction():
        for name in ("ap_credit_ledger", "ap_skill_artifacts", "ap_runs"):
            if await table_exists(conn, "public", name):
                await truncate(conn, "public", name)
            else:
                print(f"  skip public.{name} (missing)")


async def preview(conn: asyncpg.Connection, label: str) -> None:
    db = await conn.fetchval("SELECT current_database()")
    print(f"\n=== PREVIEW {label}: {db} ===")
    tables = await list_wipe_tables(conn)
    for schema, name in tables:
        print(f"  {schema}.{name}")
    for schema, name in (
        ("repository", REPO_ITEMS),
        ("dbo", EZFB_ITEMS),
        ("public", EZFB_ITEMS),
        ("public", "ap_runs"),
        ("public", "ap_skill_artifacts"),
        ("public", "ap_credit_ledger"),
    ):
        exists = await table_exists(conn, schema, name)
        print(f"  {schema}.{name} exists={exists}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Wipe EZOFIS workflow tickets on live Postgres")
    parser.add_argument("--execute", action="store_true", help="Actually truncate (default is preview)")
    parser.add_argument("--host", default=os.environ.get("PGHOST", DEFAULT_HOST))
    parser.add_argument("--user", default=os.environ.get("PGUSER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD", ""))
    parser.add_argument("--tenant-id", default="", help="Tenant guid. With --workflow-id, only that workflow is cleared.")
    parser.add_argument("--workflow-id", default="", help="Workflow guid. Clears only that workflow's ticket tables.")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass(f"Postgres password for {args.user}@{args.host}: ")

    tenant_id = (args.tenant_id or TENANT).strip()
    workflow_id = (args.workflow_id or "").strip()
    one_workflow = bool(workflow_id)
    if one_workflow and not args.tenant_id.strip():
        raise SystemExit("--tenant-id is required with --workflow-id")

    print(f"host={args.host}")
    print(f"user={args.user}")
    print(f"tenant={tenant_id}")
    if one_workflow:
        print(f"workflow={workflow_id}")
    print(f"mode={'EXECUTE' if args.execute else 'PREVIEW'}")

    fallback_db = AP_TENANT_DB if tenant_id == TENANT else ""
    catalog = await connect(args.host, args.user, password, CATALOG_DB)
    try:
        app_db = await resolve_app_database(catalog, args.host, tenant_id, fallback_db or AP_TENANT_DB)
        if not one_workflow:
            await preview(catalog, "catalog (for reference only)")
    finally:
        await catalog.close()

    if one_workflow and app_db == AP_TENANT_DB and tenant_id != TENANT:
        raise SystemExit("Could not resolve this tenant's database from catalog. Nothing deleted.")

    app = await connect(args.host, args.user, password, app_db)
    try:
        if one_workflow:
            await preview_one_workflow(app, workflow_id)
            if args.execute:
                await wipe_one_workflow(app, workflow_id)
        else:
            await preview(app, "APP")
            if args.execute:
                await wipe_app_db(app)
    finally:
        await app.close()

    if one_workflow:
        print("\nDone." if args.execute else "\nPreview only. Re-run with --execute to wipe this workflow.")
        return 0

    # AP artifact DB (may be same as app_db)
    if app_db != AP_TENANT_DB:
        ap = await connect(args.host, args.user, password, AP_TENANT_DB)
        try:
            await preview(ap, "AP tenant")
            if args.execute:
                await wipe_ap_tenant_db(ap)
        finally:
            await ap.close()
    elif args.execute:
        # already wiped public.ap_* inside wipe_app_db if present
        pass

    print(
        """
=== Redis (run on agents VM / compose host) ===
  docker compose exec redis redis-cli FLUSHDB
  # or: redis-cli -u "$REDIS_URL" FLUSHDB

Done."""
    )
    if not args.execute:
        print("Preview only. Re-run with --execute to wipe.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except asyncpg.InvalidPasswordError:
        print(
            "ERROR: password rejected.\n"
            "Try:  $env:PGUSER='postgrev6southindadmin'\n"
            "Then run again, or reset the flexible-server admin password in Azure.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
