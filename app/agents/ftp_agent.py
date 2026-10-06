"""FTP agent — Invoice Scorer → File Fetcher (skipped for multipart uploads) →
File Preparation → Folder Mover.

A document is unprocessed when more than `ftp_unprocessed_null_ratio` of its
invoice values (header fields + line item fields) are null or empty. The renamed
document and `{unique ref}.json` go over SFTP to the processed / unprocessed folder.
"""
from __future__ import annotations

import logging
import posixpath
from typing import Any, Optional

from app.classification_skills.contract import numeric_id
from app.config import Settings, get_settings
from app.tools.file_fetcher import fetch_file_by_path
from app.tools.file_preparation import prepare_files
from app.tools.folder_mover import move_to_folder
from app.tools.invoice_mapping import (
    FTP_AGENT_NAME,
    document_type,
    extraction_confidence,
    map_invoice_header,
    map_line_items,
    now_iso,
    remark,
)
from app.tools.invoice_scorer import score_invoice

logger = logging.getLogger("orchestrator.ftp_agent")

AGENT_NAME = FTP_AGENT_NAME
STATUS_SUCCESS = "SUCCESS"
STATUS_QUEUED = "QUEUED"


class FtpAgent:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    async def _document(self, job: dict[str, Any]) -> tuple[Optional[bytes], Optional[str], Optional[str], Optional[str]]:
        """(bytes, file name, content type, error) — the multipart upload, else the File Fetcher."""
        if job.get("file_bytes") is not None:
            return job["file_bytes"], job.get("filename"), job.get("content_type"), None
        fetched = await fetch_file_by_path(
            tenant_id=str(job.get("tenant_id") or ""),
            path=job.get("filepath"),
            login_email=job.get("login_email"),
            login_password=job.get("login_password"),
        )
        if fetched["status"] != "SUCCEEDED":
            return None, None, None, f"File fetch failed: {fetched['error']}"
        return fetched["file_bytes"], fetched["fileName"], fetched["contentType"], None

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """POST /chat intent=ftp."""
        job = document_job or {}
        ocr_json = job.get("ocr_json") or {}
        remarks = job.get("remarks") or {}
        processed_at = now_iso()

        scored = score_invoice(ocr_json, settings=self._cfg())
        processed = scored["valid"]
        ratio = scored["null_ratio"]

        blob_path = job.get("filepath") or job.get("filename")
        source_name = posixpath.basename((blob_path or "").replace("\\", "/"))

        error: Optional[str] = None
        delivered_at: Optional[str] = None
        ref_no: Optional[str] = None
        remote_dir: Optional[str] = None
        try:
            data, file_name, content_type, error = await self._document(job)
            if error is None:
                prepared = prepare_files(
                    ocr_json,
                    valid=processed,
                    null_ratio=ratio,
                    file_bytes=data,
                    filename=file_name or source_name,
                    content_type=content_type,
                    remarks=remarks,
                    filepath=job.get("filepath"),
                    tenant_id=job.get("tenant_id"),
                    workflow_id=job.get("workflow_id"),
                    repository_id=job.get("repository_id"),
                    instance_id=job.get("instance_id"),
                    env_type=job.get("env_type"),
                    document_type=job.get("document_type"),
                    model_display=job.get("model_display"),
                )
                ref_no = prepared["unique_ref"]
                moved = await move_to_folder(
                    files=prepared["files"], valid=processed, sftp=job.get("sftp"), settings=self._cfg()
                )
                remote_dir = moved["remote_dir"]
                if moved["status"] == "SUCCEEDED":
                    delivered_at = moved["delivered_at"]
                else:
                    error = moved["error"]
        except Exception as exc:
            logger.warning("ftp_delivery_failed", extra={"error_type": type(exc).__name__})
            error = f"SFTP upload failed: {exc}"

        ref_no = ref_no or (posixpath.splitext(source_name)[0] if source_name else None) or job.get("instance_id") or "document"
        logger.info(
            "ftp_agent_done",
            extra={
                "ref_no": ref_no,
                "null_ratio": round(ratio, 3),
                "processed": processed,
                "remote_dir": remote_dir,
                "delivered": delivered_at is not None,
            },
        )

        contract = {
            "agent": AGENT_NAME,
            "FTP status": STATUS_SUCCESS if error is None else STATUS_QUEUED,
            "documentType": document_type(job, ocr_json.get("invoice_header")),
            "processedAt": processed_at,
            "source": {
                "blobPath": blob_path,
                "fileName": remark(remarks, "Received Filename") or source_name or None,
                "emailFrom": remark(remarks, "From", "emailFrom"),
                "emailSubject": remark(remarks, "Email_Subject", "emailSubject", "Subject"),
            },
            "environment": {
                "envType": job.get("env_type"),
                "tenantId": numeric_id(job.get("tenant_id")),
                "workflowId": numeric_id(job.get("workflow_id")),
                "repositoryId": numeric_id(job.get("repository_id")),
                "instanceId": job.get("instance_id"),
            },
            "extraction": {
                "model": job.get("model_display"),
                "confidence": extraction_confidence(ocr_json, ratio),
                "invoiceHeader": map_invoice_header(ocr_json.get("invoice_header")),
                "lineItems": map_line_items(ocr_json.get("line_items")),
            },
            "deliveredAt": delivered_at,
            "ERROR": error,
        }
        folder = "processed" if processed else "unprocessed"
        reply = (
            f"Delivered {ref_no} to the {folder} folder."
            if error is None
            else f"FTP delivery queued for {ref_no}: {error}"
        )
        return {"reply": reply, "usage": None, "document_id": blob_path, "ftp_result": contract}
