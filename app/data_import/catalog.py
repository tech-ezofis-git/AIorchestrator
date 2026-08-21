"""Resolve tenant Postgres URL from the ezofis catalog Tenants table."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.data_import.ident import parse_connection_kv

logger = logging.getLogger("orchestrator.data_import")


class CatalogUnavailableError(Exception):
    """Catalog DB URL missing or connection failed."""


class TenantConnectionNotFoundError(Exception):
    """Tenant row missing, inactive, or ConnectionString empty/unusable."""


# Quoted EF/Npgsql identifiers first. Unquoted catalog.tenants does not exist
# on Azure (table is catalog."Tenants").
_TENANT_QUERIES = (
    """
    SELECT "Id", "Name", "Email", "ConnectionString"
    FROM catalog."Tenants"
    WHERE "Id" = CAST(:tid AS uuid)
      AND coalesce("IsActive", true) = true
    LIMIT 1
    """,
    """
    SELECT "Id", "Name", "Email", "ConnectionString"
    FROM catalog."Tenants"
    WHERE replace(lower("Id"::text), '-', '') = replace(lower(:tid), '-', '')
      AND coalesce("IsActive", true) = true
    LIMIT 1
    """,
    """
    SELECT "Id", "Name", "Email", "ConnectionString"
    FROM catalog."Tenants"
    WHERE "Id" = CAST(:tid AS uuid)
    LIMIT 1
    """,
)


def ensure_azure_ssl(url: str) -> str:
    """Match asyncpg catalog_pool_kwargs: Azure hosts need sslmode=require."""
    raw = (url or "").strip()
    if not raw:
        return raw
    lowered = raw.lower()
    if "sslmode=" in lowered:
        return raw
    if "azure.com" in lowered or "ssl=true" in lowered:
        sep = "&" if "?" in raw else "?"
        return f"{raw}{sep}sslmode=require"
    return raw


def sqlalchemy_url_from_connection_string(conn_str: str) -> str:
    kv = parse_connection_kv(conn_str)
    host = kv.get("host") or kv.get("server") or kv.get("data source")
    port = kv.get("port", "5432")
    database = kv.get("database") or kv.get("initial catalog")
    user = kv.get("username") or kv.get("user id") or kv.get("uid")
    password = kv.get("password") or kv.get("pwd")
    if not all([host, database, user, password]):
        raise ValueError("Incomplete PostgreSQL connection string")
    sslmode = kv.get("sslmode") or kv.get("ssl mode")
    if sslmode:
        sslmode = str(sslmode).strip().lower()
    if not sslmode:
        sslmode = "require" if host and "postgres.database.azure.com" in host else "prefer"
    return (
        f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(database)}?sslmode={sslmode}"
    )


def catalog_sqlalchemy_url() -> str:
    settings = get_settings()
    raw = (settings.catalog_database_url or "").strip() or (settings.database_url or "").strip()
    if not raw:
        raise CatalogUnavailableError("CATALOG_DATABASE_URL is not set.")
    if "://" not in raw:
        return sqlalchemy_url_from_connection_string(raw)
    if raw.startswith("postgresql://"):
        raw = "postgresql+psycopg2://" + raw[len("postgresql://") :]
    elif raw.startswith("postgres://"):
        raw = "postgresql+psycopg2://" + raw[len("postgres://") :]
    return ensure_azure_ssl(raw)


def postgres_target_diag(raw: str) -> dict[str, Optional[str]]:
    """Host + database name only — never user/password."""
    text = (raw or "").strip()
    if not text:
        return {"host": None, "database": None}
    if "://" in text:
        parsed = urlparse(text)
        database = (parsed.path or "").lstrip("/").split("?")[0] or None
        return {"host": parsed.hostname, "database": database}
    kv = parse_connection_kv(text)
    return {
        "host": kv.get("host") or kv.get("server") or kv.get("data source"),
        "database": kv.get("database") or kv.get("initial catalog"),
    }


def catalog_env_diag() -> dict[str, Any]:
    settings = get_settings()
    catalog_url = (settings.catalog_database_url or "").strip()
    app_url = (settings.database_url or "").strip()
    return {
        "catalog_database_url_set": bool(catalog_url),
        "catalog_target": postgres_target_diag(catalog_url),
        "app_database_url_set": bool(app_url),
        "app_target": postgres_target_diag(app_url),
    }


def safe_cs_preview(cs: str) -> dict[str, Any]:
    kv = parse_connection_kv(cs)
    parse_ok = True
    try:
        _tenant_sqlalchemy_url(cs)
    except Exception:
        parse_ok = False
    keys = sorted(k for k in kv if k not in {"password", "pwd"})
    if "://" in cs and "uri" not in keys:
        keys = ["uri", *keys]
    target = postgres_target_diag(cs)
    return {
        "tenant_host": target.get("host"),
        "tenant_database": target.get("database"),
        "cs_keys": keys,
        "cs_parse_ok": parse_ok,
    }


async def resolve_tenant_connection_string(tenant_id: str, store: Any = None) -> tuple[Optional[str], dict[str, Any]]:
    """Return tenant ConnectionString plus a secret-free diagnosis of which DB was used."""
    diag: dict[str, Any] = catalog_env_diag()
    tid = (tenant_id or "").strip()
    try:
        from app.ap_skills.tenant_db import tenant_database_name

        prefix = (get_settings().ap_tenant_db_prefix or "ezofis_Tenant_").strip() or "ezofis_Tenant_"
        derived = tenant_database_name(tid, prefix=prefix)
        diag["derived_tenant_database"] = derived
        if derived:
            target = dict(diag.get("app_target") or {})
            target["database"] = derived
            diag["derived_tenant_target"] = {
                "host": (diag.get("catalog_target") or {}).get("host")
                or (diag.get("app_target") or {}).get("host"),
                "database": derived,
            }
    except Exception:
        diag["derived_tenant_database"] = None
    if store is not None:
        try:
            diag["asyncpg_pool_database"] = await store.connected_database()
        except Exception as exc:
            diag["asyncpg_pool_database"] = None
            diag["asyncpg_pool_describe_error"] = type(exc).__name__
        try:
            cs = await store.fetch_tenant_connection_string(tid)
            diag["asyncpg_pool_cs_found"] = bool(cs)
            if cs:
                diag.update(safe_cs_preview(cs))
                diag["lookup"] = "asyncpg_pool"
                return cs, diag
        except Exception as exc:
            diag["asyncpg_pool_cs_found"] = False
            diag["asyncpg_pool_lookup_error"] = type(exc).__name__
    catalog_url = (get_settings().catalog_database_url or "").strip()
    in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if catalog_url and not in_pytest:
        try:
            import asyncpg

            from app.catalog.url import catalog_pool_kwargs, normalize_catalog_url

            conn = await asyncpg.connect(
                normalize_catalog_url(catalog_url),
                **catalog_pool_kwargs(catalog_url),
            )
            try:
                dbrow = await conn.fetchrow("SELECT current_database() AS db")
                diag["direct_catalog_database"] = dbrow["db"] if dbrow else None
                row = await conn.fetchrow(
                    """
                    SELECT "ConnectionString" AS connection_string
                    FROM catalog."Tenants"
                    WHERE "Id" = $1::uuid
                    LIMIT 1
                    """,
                    tid,
                )
            finally:
                await conn.close()
            cs = None
            if row is not None:
                try:
                    cs = row["connection_string"]
                except (KeyError, TypeError):
                    try:
                        cs = row["ConnectionString"]
                    except (KeyError, TypeError, IndexError):
                        cs = row[0] if len(row) else None
            cs = str(cs or "").strip() or None
            diag["direct_catalog_cs_found"] = bool(cs)
            if cs:
                diag.update(safe_cs_preview(cs))
                diag["lookup"] = "direct_catalog_url"
                return cs, diag
        except Exception as exc:
            logger.warning(
                "direct_catalog_tenant_lookup_failed",
                extra={"error_type": type(exc).__name__},
            )
            diag["direct_catalog_error"] = type(exc).__name__
    diag["lookup"] = "none"
    return None, diag


@lru_cache(maxsize=1)
def _catalog_engine():
    return create_engine(catalog_sqlalchemy_url(), pool_pre_ping=True)


def _fetch_catalog_tenant_row(conn, tid: str):
    last_err: Optional[Exception] = None
    for sql in _TENANT_QUERIES:
        try:
            row = conn.execute(text(sql), {"tid": tid}).fetchone()
            if row:
                return row
        except Exception as exc:
            last_err = exc
            try:
                conn.rollback()
            except Exception:
                pass
    if last_err:
        logger.warning("catalog_tenant_queries_failed", extra={"error_type": type(last_err).__name__})
    return None


def _tenant_sqlalchemy_url(tenant_cs: str) -> str:
    if "://" not in tenant_cs:
        return sqlalchemy_url_from_connection_string(tenant_cs)
    if tenant_cs.startswith("postgresql://"):
        tenant_cs = "postgresql+psycopg2://" + tenant_cs[len("postgresql://") :]
    elif tenant_cs.startswith("postgres://"):
        tenant_cs = "postgresql+psycopg2://" + tenant_cs[len("postgres://") :]
    return ensure_azure_ssl(tenant_cs)


def fetch_tenant_catalog_row(tenant_id: str) -> Optional[dict[str, Any]]:
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    try:
        engine = _catalog_engine()
    except CatalogUnavailableError:
        raise
    except Exception as exc:
        logger.warning("catalog_engine_failed", extra={"error_type": type(exc).__name__})
        raise CatalogUnavailableError("Catalog database is unavailable.") from exc
    try:
        with engine.connect() as conn:
            row = _fetch_catalog_tenant_row(conn, tid)
            if row is None:
                return None
            tenant_cs = (row[3] or "").strip()
            if not tenant_cs:
                return None
            try:
                sqlalchemy_url = _tenant_sqlalchemy_url(tenant_cs)
            except Exception as exc:
                logger.warning(
                    "catalog_tenant_connection_string_invalid",
                    extra={"error_type": type(exc).__name__},
                )
                return None
            return {
                "id": str(row[0]),
                "name": row[1],
                "email": row[2],
                "sqlalchemy_url": sqlalchemy_url,
            }
    except CatalogUnavailableError:
        raise
    except Exception as exc:
        logger.warning("catalog_tenant_lookup_failed", extra={"error_type": type(exc).__name__})
        raise CatalogUnavailableError("Catalog database is unavailable.") from exc


def tenant_engine_url_from_app_settings(tenant_id: str) -> str:
    """Same host/user as DATABASE_URL, database ezofis_Tenant_<first 8 of tenantId>."""
    from app.ap_skills.tenant_db import replace_database_name, tenant_database_name

    settings = get_settings()
    raw = (settings.catalog_database_url or "").strip() or (settings.database_url or "").strip()
    if not raw:
        raise CatalogUnavailableError("DATABASE_URL is not set.")
    prefix = (settings.ap_tenant_db_prefix or "ezofis_Tenant_").strip() or "ezofis_Tenant_"
    db_name = tenant_database_name(tenant_id, prefix=prefix)
    if not db_name:
        raise TenantConnectionNotFoundError("No tenant connection found.")
    if "://" not in raw:
        raw = sqlalchemy_url_from_connection_string(raw)
    swapped = replace_database_name(raw, db_name)
    if swapped.startswith("postgresql://"):
        swapped = "postgresql+psycopg2://" + swapped[len("postgresql://") :]
    elif swapped.startswith("postgres://"):
        swapped = "postgresql+psycopg2://" + swapped[len("postgres://") :]
    return ensure_azure_ssl(swapped)


def create_tenant_engine(tenant_id: str, connection_string: Optional[str] = None):
    cs = (connection_string or "").strip()
    if cs:
        try:
            return create_engine(_tenant_sqlalchemy_url(cs), pool_pre_ping=True)
        except Exception as exc:
            logger.warning("tenant_engine_from_cs_failed", extra={"error_type": type(exc).__name__})
            raise TenantConnectionNotFoundError("No tenant connection found.") from exc
    try:
        return create_engine(tenant_engine_url_from_app_settings(tenant_id), pool_pre_ping=True)
    except (TenantConnectionNotFoundError, CatalogUnavailableError):
        raise
    except Exception as exc:
        logger.warning("tenant_engine_from_app_settings_failed", extra={"error_type": type(exc).__name__})
    row = fetch_tenant_catalog_row(tenant_id)
    if not row or not row.get("sqlalchemy_url"):
        raise TenantConnectionNotFoundError("No tenant connection found.")
    return create_engine(row["sqlalchemy_url"], pool_pre_ping=True)
