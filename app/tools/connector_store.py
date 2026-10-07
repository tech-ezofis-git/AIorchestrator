"""SFTP settings from a tenant connector — dbo."connector".ConfigJson in ezofis_Tenant_{first 8 of tenant id}.

ConfigJson: {"host", "username", "password", "sftp_port" (or "port"), "ftps_port"}.
Read-only; the tenant DB lives on the same server as CATALOG_DATABASE_URL.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import asyncpg

from app.ap_skills.tenant_db import DEFAULT_TENANT_DB_PREFIX, replace_database_name, tenant_database_name
from app.catalog.url import catalog_pool_kwargs, normalize_catalog_url
from app.config import Settings, get_settings

_TIMEOUT_SECONDS = 10.0


class ConnectorError(Exception):
    """The connector could not be loaded; the message is safe to return to the caller."""


async def load_sftp_connector(
    tenant_id: Optional[str], connector_id: str, settings: Optional[Settings] = None
) -> dict[str, Any]:
    """{host, port, username, password} from the connector row. Raises ConnectorError."""
    settings = settings or get_settings()
    try:
        cid = str(uuid.UUID(str(connector_id).strip()))
    except ValueError as exc:
        raise ConnectorError(f"connector_id '{connector_id}' is not a valid id.") from exc
    prefix = (settings.ap_tenant_db_prefix or DEFAULT_TENANT_DB_PREFIX).strip() or DEFAULT_TENANT_DB_PREFIX
    db_name = tenant_database_name(str(tenant_id or ""), prefix=prefix)
    if db_name is None:
        raise ConnectorError("A tenant id (GUID) is required to look up the connector.")
    base = settings.catalog_database_url or settings.database_url
    raw_url = replace_database_name(base, db_name)

    try:
        conn = await asyncpg.connect(
            normalize_catalog_url(raw_url), timeout=_TIMEOUT_SECONDS, **catalog_pool_kwargs(raw_url)
        )
    except Exception as exc:
        raise ConnectorError(f"Unable to open tenant database {db_name}: {type(exc).__name__}.") from exc
    try:
        row = await conn.fetchrow(
            'SELECT "ConfigJson" FROM dbo."connector" '
            'WHERE "Id" = $1::uuid AND NOT COALESCE("IsDeleted", false)',
            cid,
        )
    except Exception as exc:
        raise ConnectorError(f"Connector lookup failed: {type(exc).__name__}.") from exc
    finally:
        await conn.close()

    if row is None:
        raise ConnectorError(f"Connector {cid} was not found for this tenant.")
    try:
        config = json.loads(row["ConfigJson"] or "")
    except ValueError as exc:
        raise ConnectorError(f"Connector {cid} ConfigJson is not valid JSON.") from exc
    if not isinstance(config, dict):
        raise ConnectorError(f"Connector {cid} ConfigJson must be a JSON object.")

    missing = [k for k in ("host", "username", "password") if not str(config.get(k) or "").strip()]
    if missing:
        raise ConnectorError(f"Connector {cid} ConfigJson is missing: {', '.join(missing)}.")
    port = config.get("sftp_port") or config.get("port")
    try:
        port = int(port) if port not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ConnectorError(f"Connector {cid} sftp_port must be a number.") from exc
    return {
        "host": str(config["host"]).strip(),
        "port": port,
        "username": str(config["username"]).strip(),
        "password": str(config["password"]),
    }
