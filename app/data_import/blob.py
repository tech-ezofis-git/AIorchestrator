"""Download an import file from tenant Azure blob storage."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger("orchestrator.data_import")


def tenant_container_name(tenant_id: str, prefix: Optional[str] = None) -> str:
    raw = str(tenant_id).strip().lower().replace("-", "")
    prefix = (prefix or get_settings().azure_blob_container_prefix or "ezts").lower()
    if raw.startswith(prefix):
        return raw
    return f"{prefix}{raw}"


def download_blob_bytes(tenant_id: str, filepath: str) -> bytes:
    conn = (get_settings().azure_storage_connection_string or "").strip()
    if not conn:
        raise HTTPException(
            status_code=503,
            detail="Azure blob storage is not configured (AZURE_STORAGE_CONNECTION_STRING).",
        )
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="azure-storage-blob is not installed.") from exc

    container = tenant_container_name(tenant_id)
    try:
        service = BlobServiceClient.from_connection_string(conn)
        blob = service.get_blob_client(container=container, blob=filepath)
        if not blob.exists():
            raise HTTPException(
                status_code=404,
                detail="Import file was not found in blob storage.",
            )
        data = blob.download_blob().readall()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("data_import_blob_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=502, detail="Failed to download the import file.") from exc
    if not data:
        raise HTTPException(status_code=400, detail="Import file is empty.")
    logger.info("data_import_blob_downloaded", extra={"bytes": len(data)})
    return data
