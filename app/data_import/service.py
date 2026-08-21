"""Run ezDataImport: resolve tenant DB, download xlsx, map columns, upsert."""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.data_import.blob import download_blob_bytes
from app.data_import.catalog import (
    CatalogUnavailableError,
    TenantConnectionNotFoundError,
    create_tenant_engine,
)
from app.data_import.ident import resolve_import_table_name
from app.data_import.models import DataImportRequest
from app.data_import.xlsx_import import import_xlsx_bytes

logger = logging.getLogger("orchestrator.data_import")


def run_data_import(request: DataImportRequest) -> dict[str, str]:
    extension = (request.fileName or "").rsplit(".", 1)[-1].lower()
    if extension != "xlsx":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {extension}. Only xlsx is supported.",
        )
    try:
        table_name = resolve_import_table_name(request.formId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid formId for PO master table.") from None
    try:
        engine = create_tenant_engine(request.tenantId)
    except TenantConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="No tenant connection found.") from None
    except CatalogUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Catalog database is unavailable.",
        ) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="No tenant connection found.") from None
    file_bytes = download_blob_bytes(request.tenantId, request.filepath)
    logger.info("data_import_started", extra={"rows_hint": table_name})
    try:
        return import_xlsx_bytes(engine, request, table_name, file_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("data_import_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=404, detail="Import failed.") from exc
