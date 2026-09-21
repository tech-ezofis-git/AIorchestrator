#!/usr/bin/env python3
"""Stand-alone wipe for EZOFIS AP tickets on postgrev6southinddb.

Prompts for DB password (does not use stale .env password unless you pass it).

Preview:
  py -3 scripts/run_wipe_ezofis_tickets.py

Execute:
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


async def resolve_app_database(catalog: asyncpg.Connection, host: str) -> str:
    """Prefer catalog.Tenants.ConnectionString database; fallback to AP tenant DB name."""
    row = await catalog.fetchrow(
        """
        SELECT "ConnectionString" AS cs
        FROM catalog."Tenants"
        WHERE replace(lower("Id"::text), '-', '') = replace(lower($1::text), '-', '')
        LIMIT 1
        """,
        TENANT,
    )
    if not row or not row["cs"]:
        print(f"  WARN: no ConnectionString; using {AP_TENANT_DB}")
        return AP_TENANT_DB
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
    print(f"  WARN: could not parse ConnectionString; using {AP_TENANT_DB}")
    return AP_TENANT_DB


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
    parser = argparse.ArgumentParser(description="Wipe EZOFIS AP tickets on live Postgres")
    parser.add_argument("--execute", action="store_true", help="Actually truncate (default is preview)")
    parser.add_argument("--host", default=os.environ.get("PGHOST", DEFAULT_HOST))
    parser.add_argument("--user", default=os.environ.get("PGUSER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD", ""))
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass(f"Postgres password for {args.user}@{args.host}: ")

    print(f"host={args.host}")
    print(f"user={args.user}")
    print(f"mode={'EXECUTE' if args.execute else 'PREVIEW'}")

    catalog = await connect(args.host, args.user, password, CATALOG_DB)
    try:
        app_db = await resolve_app_database(catalog, args.host)
        await preview(catalog, "catalog (for reference only)")
    finally:
        await catalog.close()

    app = await connect(args.host, args.user, password, app_db)
    try:
        await preview(app, "APP")
        if args.execute:
            await wipe_app_db(app)
    finally:
        await app.close()

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
