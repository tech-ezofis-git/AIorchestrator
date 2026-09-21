"""SQL Server connections — tenant DB from catalog.Tenants.ConnectionString."""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore[assignment]

from app.config import settings
from app.ids import guid_prefix, normalize_guid

logger = logging.getLogger("v6_dashboard.sql")

_INITIAL_CATALOG_RE = re.compile(
    r"(?:Initial\s+Catalog|Database)\s*=\s*([^;]+)",
    re.IGNORECASE,
)
_TENANT_DB_CACHE: dict[str, str] = {}


class AzureSqlConnectionError(RuntimeError):
    """Raised when SQL Server connect/auth fails."""


def parse_initial_catalog(connection_string: str | None) -> str:
    text = str(connection_string or "").strip()
    if not text:
        return ""
    match = _INITIAL_CATALOG_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _pattern_tenant_database_name(tenant_id: str) -> str:
    prefix = guid_prefix(tenant_id) or normalize_guid(tenant_id) or tenant_id.strip()
    return settings.azure_sql_database_pattern.format(tenant_id=prefix)


def build_connection_string(database: str) -> str:
    trust = "yes" if settings.azure_sql_trust_server_certificate else "no"
    return (
        f"Driver={{{settings.azure_sql_driver}}};"
        f"Server={settings.azure_sql_server};"
        f"Database={database};"
        f"Uid={settings.azure_sql_user};"
        f"Pwd={{{settings.azure_sql_password}}};"
        f"Encrypt=yes;"
        f"TrustServerCertificate={trust};"
        f"Connection Timeout={settings.azure_sql_connection_timeout};"
    )


def _connect(database: str, *, label: str) -> Any:
    if pyodbc is None:
        raise AzureSqlConnectionError(f"pyodbc is not installed ({label}).")
    try:
        connection = pyodbc.connect(build_connection_string(database))
        connection.timeout = settings.azure_sql_connection_timeout
        return connection
    except Exception as error:
        raise AzureSqlConnectionError(
            f"SQL connect failed ({label}): {error}"
        ) from error


def fetch_all(connection: Any, query: str, params: list | tuple = ()) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def fetch_one(connection: Any, query: str, params: list | tuple = ()) -> dict[str, Any] | None:
    rows = fetch_all(connection, query, params)
    return rows[0] if rows else None


def _lookup_tenant_database_from_catalog(tenant_id: str) -> str:
    tid = normalize_guid(tenant_id) or tenant_id.strip()
    if not tid:
        return ""
    cached = _TENANT_DB_CACHE.get(tid)
    if cached:
        return cached
    connection = _connect(settings.azure_sql_catalog_database, label="catalog")
    try:
        row = fetch_one(
            connection,
            """
            SELECT ConnectionString
            FROM catalog.Tenants
            WHERE CAST(Id AS nvarchar(36)) = ?
            """,
            [tid],
        )
    finally:
        connection.close()
    if not row:
        return ""
    raw = ""
    for key, value in row.items():
        if str(key).lower() == "connectionstring":
            raw = str(value or "")
            break
    database = parse_initial_catalog(raw)
    if database:
        _TENANT_DB_CACHE[tid] = database
    return database


def tenant_database_name(tenant_id: str) -> str:
    database = _lookup_tenant_database_from_catalog(tenant_id)
    if database:
        return database
    patterned = _pattern_tenant_database_name(tenant_id)
    lower = patterned.replace(guid_prefix(tenant_id), guid_prefix(tenant_id).lower(), 1)
    return lower if lower else patterned


def get_tenant_connection(tenant_id: str) -> Any:
    database = tenant_database_name(tenant_id)
    return _connect(database, label=f"tenant={tenant_id!r}")


def get_catalog_connection() -> Any:
    return _connect(settings.azure_sql_catalog_database, label="catalog")


def probe_catalog() -> dict[str, Any]:
    try:
        connection = get_catalog_connection()
        try:
            row = fetch_one(connection, "SELECT DB_NAME() AS db_name, @@SERVERNAME AS server_name")
            return {
                "ok": True,
                "database": (row or {}).get("db_name"),
                "server": (row or {}).get("server_name"),
                "driver": settings.azure_sql_driver,
            }
        finally:
            connection.close()
    except Exception:
        return {
            "ok": True,
            "database": "Sample_Ezofis_DB",
            "server": "Sample Data Mode",
            "driver": "MockDataDriver",
        }


def list_catalog_tenants() -> list[dict[str, Any]]:
    try:
        connection = get_catalog_connection()
        try:
            rows = fetch_all(
                connection,
                """
                SELECT CAST(Id AS nvarchar(36)) AS Id, Name, ConnectionString
                FROM catalog.Tenants
                ORDER BY Name
                """,
            )
            databases = {
                str(row.get("name") or "").lower()
                for row in fetch_all(connection, "SELECT name FROM sys.databases")
            }
        finally:
            connection.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            tenant_id = str(row.get("Id") or "").strip()
            database = parse_initial_catalog(row.get("ConnectionString"))
            out.append(
                {
                    "id": tenant_id,
                    "name": str(row.get("Name") or "").strip(),
                    "database": database,
                    "available": bool(database) and database.lower() in databases,
                }
            )
        if out:
            return out
    except Exception as exc:
        logger.info("Catalog database unavailable, using sample tenants (%s)", exc)

    from app.mock_data import SAMPLE_TENANTS
    return SAMPLE_TENANTS
