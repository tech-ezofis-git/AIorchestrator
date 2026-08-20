"""Resolve tenant Postgres URL from the ezofis catalog Tenants table."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.data_import.ident import parse_connection_kv

logger = logging.getLogger("orchestrator.data_import")

_TENANT_QUERIES = (
    """
    SELECT "Id", "Name", "Email", "ConnectionString"
    FROM catalog."Tenants"
    WHERE lower("Id"::text) = lower(:tid)
      AND coalesce("IsActive"::int, 1) = 1
    LIMIT 1
    """,
    """
    SELECT id, name, email, "ConnectionString"
    FROM catalog."Tenants"
    WHERE lower(id::text) = lower(:tid)
      AND coalesce("IsActive"::int, 1) = 1
    LIMIT 1
    """,
    """
    SELECT id, name, email, connectionstring
    FROM catalog.tenants
    WHERE lower(id::text) = lower(:tid)
      AND coalesce(isactive::int, 1) = 1
    LIMIT 1
    """,
    """
    SELECT id, name, email, connection_string
    FROM catalog.tenants
    WHERE lower(id::text) = lower(:tid)
      AND coalesce(is_active::int, 1) = 1
    LIMIT 1
    """,
    """
    SELECT "Id", "Name", "Email", "ConnectionString"
    FROM catalog."Tenants"
    WHERE lower("Id"::text) = lower(:tid)
    LIMIT 1
    """,
    """
    SELECT id, name, email, "ConnectionString"
    FROM catalog."Tenants"
    WHERE lower(id::text) = lower(:tid)
    LIMIT 1
    """,
)


def sqlalchemy_url_from_connection_string(conn_str: str) -> str:
    kv = parse_connection_kv(conn_str)
    host = kv.get("host") or kv.get("server") or kv.get("data source")
    port = kv.get("port", "5432")
    database = kv.get("database") or kv.get("initial catalog")
    user = kv.get("username") or kv.get("user id") or kv.get("uid")
    password = kv.get("password") or kv.get("pwd")
    if not all([host, database, user, password]):
        raise ValueError("Incomplete PostgreSQL connection string")
    sslmode = kv.get("sslmode")
    if not sslmode:
        sslmode = "require" if host and "postgres.database.azure.com" in host else "prefer"
    return (
        f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(database)}?sslmode={sslmode}"
    )


def catalog_sqlalchemy_url() -> str:
    raw = (get_settings().catalog_database_url or "").strip()
    if not raw:
        raise ValueError("CATALOG_DATABASE_URL is not set.")
    if "://" not in raw:
        return sqlalchemy_url_from_connection_string(raw)
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg2://" + raw[len("postgresql://") :]
    return raw


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


def fetch_tenant_catalog_row(tenant_id: str) -> Optional[dict[str, Any]]:
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    try:
        with _catalog_engine().connect() as conn:
            row = _fetch_catalog_tenant_row(conn, tid)
            if row is None:
                return None
            tenant_cs = (row[3] or "").strip()
            if not tenant_cs:
                return None
            return {
                "id": str(row[0]),
                "name": row[1],
                "email": row[2],
                "sqlalchemy_url": sqlalchemy_url_from_connection_string(tenant_cs)
                if "://" not in tenant_cs
                else (
                    "postgresql+psycopg2://" + tenant_cs[len("postgresql://") :]
                    if tenant_cs.startswith("postgresql://")
                    else tenant_cs
                ),
            }
    except Exception as exc:
        logger.warning("catalog_tenant_lookup_failed", extra={"error_type": type(exc).__name__})
        return None


def create_tenant_engine(tenant_id: str):
    row = fetch_tenant_catalog_row(tenant_id)
    if not row or not row.get("sqlalchemy_url"):
        raise ValueError("No tenant connection found.")
    return create_engine(row["sqlalchemy_url"], pool_pre_ping=True)
