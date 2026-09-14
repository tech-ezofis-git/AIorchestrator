#!/usr/bin/env python3
"""Wipe EZOFIS AP tickets + related rows for tenant b843b988, then clear Redis.

Usage:
  py -3 scripts/wipe_ezofis_ap_tickets.py --preview
  py -3 scripts/wipe_ezofis_ap_tickets.py --execute
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.ap_skills.tenant_db import replace_database_name, tenant_database_name
from app.catalog.store import CatalogStore
from app.catalog.url import (
    asyncpg_url_from_connection_string,
    catalog_pool_kwargs,
    normalize_catalog_url,
)
from app.config import get_settings

TENANT = "b843b988-00ec-44e3-aca2-b8470133ef63"

WORKFLOW_PREDICATE = """
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

# Extra patterns often holding ticket/run residue (preview + optional wipe).
EXTRA_TICKET_LIKE = """
table_type = 'BASE TABLE'
AND (
     (table_schema = 'workflow' AND (
        table_name ILIKE '%instance%'
     OR table_name ILIKE '%ticket%'
     OR table_name ILIKE '%apagent%'
     OR table_name ILIKE '%ap_agent%'
     OR table_name ILIKE '%validation%'
     OR table_name ILIKE '%transaction%'
     OR table_name ILIKE '%inbox%'
     OR table_name ILIKE '%request%'
     ))
  OR (table_schema = 'public' AND table_name IN (
        'ap_runs', 'ap_skill_artifacts', 'ap_credit_ledger', 'ap_run_credits'
     ))
)
"""


async def connect_pools():
    settings = get_settings()
    catalog_url = normalize_catalog_url(settings.catalog_database_url or "")
    catalog_pool = await asyncpg.create_pool(
        catalog_url,
        min_size=1,
        max_size=2,
        **catalog_pool_kwargs(catalog_url),
    )
    store = CatalogStore(catalog_pool)
    cs = await store.fetch_tenant_connection_string(TENANT)
    if not cs:
        raise SystemExit("No tenant ConnectionString for EZOFIS")
    app_dsn_raw = asyncpg_url_from_connection_string(cs)
    app_dsn = normalize_catalog_url(app_dsn_raw)
    app_pool = await asyncpg.create_pool(
        app_dsn, min_size=1, max_size=2, **catalog_pool_kwargs(app_dsn_raw)
    )

    # AP artifact DB (ezofis_Tenant_b843b988) — may equal app DB or differ.
    prefix = (settings.ap_tenant_db_prefix or "ezofis_Tenant_").strip()
    derived = tenant_database_name(TENANT, prefix=prefix)
    ap_pool = None
    ap_db_name = None
    if derived:
        # Prefer swapping catalog host credentials onto tenant DB name.
        try:
            swapped = replace_database_name(catalog_url, derived)
            ap_dsn = normalize_catalog_url(swapped)
            ap_pool = await asyncpg.create_pool(
                ap_dsn, min_size=1, max_size=2, **catalog_pool_kwargs(swapped)
            )
            ap_db_name = derived
        except Exception as exc:
            print(f"WARN: could not open {derived}: {type(exc).__name__}: {exc}")
    return catalog_pool, app_pool, ap_pool, ap_db_name, app_dsn_raw


async def list_matching(conn, where_sql: str):
    return await conn.fetch(
        f"""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE {where_sql}
        ORDER BY table_schema, table_name
        """
    )


async def table_exists(conn, schema: str, name: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = $1 AND table_name = $2 AND table_type = 'BASE TABLE'
        """,
        schema,
        name,
    )
    return bool(row)


async def preview(app_pool, ap_pool, ap_db_name):
    async with app_pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        print(f"\n=== APP DB: {db} ===")
        wipe = await list_matching(conn, WORKFLOW_PREDICATE)
        print(f"Script wipe targets ({len(wipe)}):")
        for r in wipe:
            print(f"  {r['table_schema']}.{r['table_name']}")

        extras = await list_matching(conn, EXTRA_TICKET_LIKE)
        wipe_set = {(r["table_schema"], r["table_name"]) for r in wipe}
        missed = [
            r
            for r in extras
            if (r["table_schema"], r["table_name"]) not in wipe_set
        ]
        print(f"\nPossible missed ticket-like tables ({len(missed)}):")
        for r in missed:
            print(f"  {r['table_schema']}.{r['table_name']}")

        for label, schema, name in (
            ("repo items", "repository", "items_a6169a5c"),
            ("ezfb AP form", "dbo", "ezfb_6e45749f_items"),
            ("ezfb public", "public", "ezfb_6e45749f_items"),
        ):
            exists = await table_exists(conn, schema, name)
            print(f"  {label}: {schema}.{name} exists={exists}")

    if ap_pool is not None:
        async with ap_pool.acquire() as conn:
            db = await conn.fetchval("SELECT current_database()")
            print(f"\n=== AP TENANT DB: {db} (expected {ap_db_name}) ===")
            for name in ("ap_runs", "ap_skill_artifacts", "ap_credit_ledger"):
                exists = await table_exists(conn, "public", name)
                print(f"  public.{name} exists={exists}")
                if exists:
                    n = await conn.fetchval(f'SELECT count(*) FROM public."{name}"')
                    print(f"    rows={n}")


async def truncate_table(conn, schema: str, name: str) -> None:
    await conn.execute(
        f'TRUNCATE TABLE "{schema}"."{name}" RESTART IDENTITY CASCADE'
    )
    print(f"  truncated {schema}.{name}")


async def execute_wipe(app_pool, ap_pool):
    async with app_pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        print(f"\nWiping APP DB {db} ...")
        async with conn.transaction():
            wipe = await list_matching(conn, WORKFLOW_PREDICATE)
            for r in wipe:
                await truncate_table(conn, r["table_schema"], r["table_name"])

            # Keep document templates; remove files attached to tickets
            if await table_exists(conn, "workflow", "WorkflowDocuments"):
                deleted = await conn.execute(
                    """
                    DELETE FROM workflow."WorkflowDocuments"
                    WHERE "WorkflowInstanceId" IS NOT NULL
                    """
                )
                print(f"  {deleted} workflow.WorkflowDocuments (instance-linked)")

            # Unlink repository files from deleted tickets
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

            if await table_exists(conn, "repository", "items_a6169a5c"):
                await truncate_table(conn, "repository", "items_a6169a5c")

            if await table_exists(conn, "dbo", "ezfb_6e45749f_items"):
                await truncate_table(conn, "dbo", "ezfb_6e45749f_items")
            elif await table_exists(conn, "public", "ezfb_6e45749f_items"):
                await truncate_table(conn, "public", "ezfb_6e45749f_items")

            # Extra: common leftovers in app DB
            for schema, name in (
                ("public", "ap_runs"),
                ("public", "ap_skill_artifacts"),
                ("public", "ap_credit_ledger"),
            ):
                if await table_exists(conn, schema, name):
                    await truncate_table(conn, schema, name)

    if ap_pool is not None:
        async with ap_pool.acquire() as conn:
            db = await conn.fetchval("SELECT current_database()")
            print(f"\nWiping AP TENANT DB {db} ...")
            async with conn.transaction():
                for name in ("ap_credit_ledger", "ap_skill_artifacts", "ap_runs"):
                    if await table_exists(conn, "public", name):
                        await truncate_table(conn, "public", name)


async def clear_redis() -> None:
    settings = get_settings()
    urls = []
    raw = (settings.redis_url or "").strip()
    if raw:
        urls.append(raw)
    # Local / compose aliases
    for alt in ("redis://localhost:6379/0", "redis://127.0.0.1:6379/0"):
        if alt not in urls:
            urls.append(alt)

    try:
        import redis.asyncio as redis
    except ImportError:
        import redis  # type: ignore

        async def _flush(url: str) -> None:
            client = redis.from_url(url, decode_responses=True)
            try:
                await asyncio.to_thread(client.flushdb)
                print(f"  Redis FLUSHDB ok: {url.split('@')[-1]}")
            finally:
                client.close()

        for url in urls:
            try:
                await _flush(url)
                return
            except Exception as exc:
                print(f"  Redis skip {url.split('@')[-1]}: {type(exc).__name__}")
        print("  WARN: no Redis reachable")
        return

    for url in urls:
        client = redis.from_url(url, decode_responses=True)
        try:
            await client.ping()
            await client.flushdb()
            print(f"  Redis FLUSHDB ok: {url.split('@')[-1]}")
            await client.aclose()
            return
        except Exception as exc:
            print(f"  Redis skip {url.split('@')[-1]}: {type(exc).__name__}")
            try:
                await client.aclose()
            except Exception:
                pass
    print("  WARN: no Redis reachable from this host")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.preview and not args.execute:
        args.preview = True

    catalog_pool, app_pool, ap_pool, ap_db_name, _ = await connect_pools()
    try:
        await preview(app_pool, ap_pool, ap_db_name)
        if args.execute:
            print("\n=== EXECUTE WIPE ===")
            await execute_wipe(app_pool, ap_pool)
            print("\n=== REDIS ===")
            await clear_redis()
            print("\nDone.")
        else:
            print("\nPreview only. Re-run with --execute to wipe.")
    finally:
        await app_pool.close()
        if ap_pool is not None:
            await ap_pool.close()
        await catalog_pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
