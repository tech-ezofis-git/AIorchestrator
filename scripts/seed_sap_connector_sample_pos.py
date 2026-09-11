"""Phase 1 live write: merge samplePurchaseOrders into tenant SAP connector ConfigJson."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import asyncio

ROOT = Path(r"D:\ezofis\v6\orchestrator")
ENV_PATH = ROOT / ".env"

SAMPLES = [
    {
        "po_number": "PO-60001",
        "vendor": "ACME Supplies",
        "total": 1500.00,
        "currency": "USD",
        "lines": [
            {"description": "Widget A", "qty": 10, "unit_price": 100, "amount": 1000},
            {"description": "Widget B", "qty": 5, "unit_price": 100, "amount": 500},
        ],
    },
    {
        "po_number": "PO-SAP-1001",
        "vendor": "Contoso Trading",
        "total": 2500.00,
        "currency": "USD",
        "lines": [
            {"description": "Service retainer", "qty": 1, "unit_price": 2500, "amount": 2500},
        ],
    },
    {
        "po_number": "PO-SAP-1002",
        "vendor": "Fabrikam Ltd",
        "total": 875.50,
        "currency": "USD",
        "lines": [
            {"description": "Parts kit", "qty": 1, "unit_price": 875.50, "amount": 875.50},
        ],
    },
]

PATCH = {
    "provider": "SAP",
    "mode": "sample",
    "samplePurchaseOrders": SAMPLES,
}


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def normalize_dsn(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("postgresql+psycopg2://"):
        raw = "postgresql://" + raw[len("postgresql+psycopg2://") :]
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    return raw


def ado_to_dsn(ado: str) -> str:
    """Host=;Database=;Username=;Password=; → postgresql://"""
    parts: dict[str, str] = {}
    for piece in ado.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        parts[k.strip().lower()] = v.strip()
    host = parts.get("host") or parts.get("server")
    db = parts.get("database") or parts.get("initial catalog")
    user = parts.get("username") or parts.get("user id") or parts.get("uid")
    password = parts.get("password") or parts.get("pwd")
    port = parts.get("port") or "5432"
    if not (host and db and user and password):
        raise ValueError("ADO connection string missing Host/Database/Username/Password")
    from urllib.parse import quote_plus

    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"


def any_to_dsn(raw: str) -> str:
    raw = (raw or "").strip()
    if "://" in raw:
        return normalize_dsn(raw)
    return ado_to_dsn(raw)


async def fetch_catalog_tenants(catalog_dsn: str) -> list[dict]:
    conn = await asyncpg.connect(catalog_dsn, ssl="require" if "sslmode=require" in catalog_dsn or "azure" in catalog_dsn.lower() else None)
    try:
        # Try common catalog shapes
        queries = [
            """
            SELECT "Id"::text AS tenant_id, "Name" AS name, "ConnectionString" AS cs
            FROM catalog."Tenants"
            WHERE COALESCE("IsDeleted", false) = false
            ORDER BY "CreatedAtUtc" NULLS LAST, "Name"
            LIMIT 20
            """,
            """
            SELECT "Id"::text AS tenant_id, "Name" AS name, "ConnectionString" AS cs
            FROM "Tenants"
            WHERE COALESCE("IsDeleted", false) = false
            ORDER BY "CreatedAtUtc" NULLS LAST, "Name"
            LIMIT 20
            """,
            """
            SELECT id::text AS tenant_id, name, connection_string AS cs
            FROM tenants
            ORDER BY created_at NULLS LAST, name
            LIMIT 20
            """,
        ]
        last_err = None
        for sql in queries:
            try:
                rows = await conn.fetch(sql)
                return [dict(r) for r in rows]
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        raise RuntimeError(f"Could not list tenants from catalog: {last_err}")
    finally:
        await conn.close()


async def ensure_catalog_sap_provider(catalog_dsn: str) -> None:
    conn = await asyncpg.connect(catalog_dsn, ssl="require" if "sslmode=require" in catalog_dsn or "azure" in catalog_dsn.lower() else None)
    try:
        await conn.execute(
            """
            INSERT INTO catalog."ConnectorProviders"
                ("Id", "ProviderCode", "DisplayName", "ClientId", "ClientSecret",
                 "AuthUrl", "TokenUrl", "Scopes", "RedirectUri", "IsActive", "CreatedAtUtc")
            VALUES
                (gen_random_uuid(), 'SAP', 'SAP', '', '', '', '', '', '', true, now())
            ON CONFLICT ("ProviderCode") DO NOTHING
            """
        )
        print("catalog: SAP ConnectorProviders ensured")
    except Exception as exc:  # noqa: BLE001
        print(f"catalog: SAP provider seed skipped ({type(exc).__name__}: {exc})")
    finally:
        await conn.close()


def ssl_kw(dsn: str) -> dict:
    # Azure PG usually needs SSL
    if "sslmode=" in dsn.lower() or "azure" in dsn.lower() or "postgres.database.azure.com" in dsn.lower():
        return {"ssl": "require"}
    parsed = urlparse(dsn)
    if parsed.hostname and "azure" in (parsed.hostname or "").lower():
        return {"ssl": "require"}
    return {}


async def merge_sap_samples(tenant_dsn: str, tenant_label: str) -> list[dict]:
    conn = await asyncpg.connect(tenant_dsn, **ssl_kw(tenant_dsn))
    try:
        rows = await conn.fetch(
            """
            SELECT "Id"::text AS id, "Name" AS name, "ProviderCode" AS provider,
                   "ConfigJson" AS config, "IsDefault" AS is_default
            FROM dbo."connector"
            WHERE "IsDeleted" = false AND upper("ProviderCode") = 'SAP'
            ORDER BY "IsDefault" DESC, "CreatedAtUtc" ASC
            """
        )
        if not rows:
            print(f"{tenant_label}: no SAP connector rows")
            return []

        updated = []
        for row in rows:
            existing: dict = {}
            raw = row["config"]
            if raw and str(raw).strip():
                try:
                    existing = json.loads(raw)
                    if not isinstance(existing, dict):
                        existing = {}
                except json.JSONDecodeError:
                    existing = {}
            merged = {**existing, **PATCH}
            new_json = json.dumps(merged, ensure_ascii=False)
            await conn.execute(
                """
                UPDATE dbo."connector"
                SET "ConfigJson" = $1, "ModifiedAtUtc" = now()
                WHERE "Id" = $2::uuid
                """,
                new_json,
                row["id"],
            )
            pos = [p["po_number"] for p in merged.get("samplePurchaseOrders") or []]
            updated.append(
                {
                    "tenant": tenant_label,
                    "connector_id": row["id"],
                    "name": row["name"],
                    "mode": merged.get("mode"),
                    "sample_po_numbers": pos,
                    "preserved_keys": sorted(set(existing) - set(PATCH)),
                }
            )
            print(
                f"{tenant_label}: updated connector {row['id']} "
                f"name={row['name']!r} pos={pos} preserved={sorted(set(existing)-set(PATCH))}"
            )
        return updated
    finally:
        await conn.close()


async def main() -> int:
    if not ENV_PATH.exists():
        print("missing .env", file=sys.stderr)
        return 1
    env = load_env(ENV_PATH)
    catalog = env.get("CATALOG_DATABASE_URL") or ""
    database = env.get("DATABASE_URL") or ""
    if not catalog and not database:
        print("No CATALOG_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    results: list[dict] = []

    if catalog:
        catalog_dsn = any_to_dsn(catalog)
        await ensure_catalog_sap_provider(catalog_dsn)
        tenants = await fetch_catalog_tenants(catalog_dsn)
        print(f"catalog tenants found: {len(tenants)}")
        # Prefer first tenant with a connection string; try until SAP found
        sap_found = False
        for t in tenants:
            cs = (t.get("cs") or "").strip()
            if not cs:
                continue
            label = f"{t.get('name') or 'tenant'}({(t.get('tenant_id') or '')[:8]})"
            try:
                dsn = any_to_dsn(cs)
            except Exception as exc:  # noqa: BLE001
                print(f"{label}: skip bad CS ({exc})")
                continue
            try:
                updated = await merge_sap_samples(dsn, label)
            except Exception as exc:  # noqa: BLE001
                print(f"{label}: merge failed ({type(exc).__name__}: {exc})")
                continue
            if updated:
                results.extend(updated)
                sap_found = True
                # Phase 1 asked for first tenant — stop after first success
                break
        if not sap_found:
            print("No SAP connector found via catalog tenant ConnectionStrings; trying DATABASE_URL tenant DBs…")

    if not results and database:
        # Try main DB and common ezofis_Tenant_* pattern is unknown without tenant id —
        # list databases if possible.
        base_dsn = any_to_dsn(database)
        conn = await asyncpg.connect(base_dsn, **ssl_kw(base_dsn))
        try:
            dbs = await conn.fetch(
                """
                SELECT datname FROM pg_database
                WHERE datistemplate = false
                  AND (datname ILIKE 'ezofis_Tenant_%' OR datname ILIKE 'ezofis_tenant_%')
                ORDER BY datname
                LIMIT 30
                """
            )
        finally:
            await conn.close()
        parsed = urlparse(base_dsn)
        for dbrow in dbs:
            dbname = dbrow["datname"]
            # swap path
            from urllib.parse import urlunparse

            dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
            try:
                updated = await merge_sap_samples(dsn, dbname)
            except Exception as exc:  # noqa: BLE001
                print(f"{dbname}: merge failed ({type(exc).__name__}: {exc})")
                continue
            if updated:
                results.extend(updated)
                break

    out_path = ROOT / "deploy" / "SAP_CONNECTOR_SAMPLE_PO_LIVE.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote summary: {out_path}")
    if not results:
        print("FAILED: no SAP connector updated", file=sys.stderr)
        return 2
    print("SUCCESS")
    for r in results:
        print(f"  connector_id={r['connector_id']} pos={r['sample_po_numbers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
