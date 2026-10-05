"""FTP agent — scores extracted invoice data, then delivers the document and
its invoice JSON over SFTP to the processed / unprocessed folder.

A document is unprocessed when more than `ftp_unprocessed_null_ratio` of its
invoice values (header fields + line item fields) are null or empty.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import posixpath
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.classification_skills.contract import normalize_document_type, numeric_id
from app.config import Settings, get_settings
from app.data_import.blob import download_blob_bytes

logger = logging.getLogger("orchestrator.ftp_agent")

AGENT_NAME = "FTP"
STATUS_SUCCESS = "SUCCESS"
STATUS_QUEUED = "QUEUED"
_PLACEHOLDER_KEYS = {"..."}

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "invoiceNumber": ("invoice No", "Invoice No", "invoice_number", "Invoice Number", "invoiceNumber"),
    "invoiceDate": ("invoice Date", "Invoice Date", "invoice_date", "invoiceDate"),
    "vendorName": ("Company Name", "supplier_name", "Vendor Name", "vendorName"),
    "currency": ("Currency", "currency"),
    "totalAmount": (
        "Grand Total",
        "Invoice Total (net)",
        "Invoice Total",
        "Total Amount",
        "total_due",
        "totalAmount",
    ),
}
_LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "description": ("Item description", "Item Description", "description", "Description"),
    "quantity": ("quantity", "Quantity", "Qty"),
    "unitPrice": ("Rate", "Unit Price", "unitPrice", "unit_price"),
    "lineAmount": ("Line Amount", "lineAmount", "Amount", "line_amount"),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none"})


def null_ratio(ocr_json: Optional[dict[str, Any]]) -> float:
    """Share of null/empty invoice values; 1.0 when there are no values at all."""
    data = ocr_json or {}
    values: list[Any] = []
    header = data.get("invoice_header")
    if isinstance(header, dict):
        values.extend(v for k, v in header.items() if k not in _PLACEHOLDER_KEYS)
    items = data.get("line_items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                values.extend(v for k, v in item.items() if k != "lineNo" and k not in _PLACEHOLDER_KEYS)
    if not values:
        return 1.0
    return sum(1 for v in values if _is_null(v)) / len(values)


def _pick(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in row and not _is_null(row[key]):
            return row[key]
    return None


def _to_number(value: Any) -> Optional[float]:
    if _is_null(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -abs(number) if negative else number


def _to_int_if_whole(value: Any) -> Any:
    number = _to_number(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _to_iso_date(value: Any) -> Optional[str]:
    if _is_null(value):
        return None
    text = str(value).strip()
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(text, dayfirst=False).date().isoformat()
    except (ValueError, OverflowError, ImportError):
        return text


def map_invoice_header(header: Any) -> dict[str, Any]:
    row = header if isinstance(header, dict) else {}
    total = _to_number(_pick(row, _HEADER_ALIASES["totalAmount"]))
    return {
        "invoiceNumber": _pick(row, _HEADER_ALIASES["invoiceNumber"]),
        "invoiceDate": _to_iso_date(_pick(row, _HEADER_ALIASES["invoiceDate"])),
        "vendorName": _pick(row, _HEADER_ALIASES["vendorName"]),
        "currency": _pick(row, _HEADER_ALIASES["currency"]),
        "totalAmount": total,
    }


def map_line_items(items: Any) -> list[dict[str, Any]]:
    rows = items if isinstance(items, list) else []
    mapped: list[dict[str, Any]] = []
    for index, item in enumerate(r for r in rows if isinstance(r, dict)):
        mapped.append(
            {
                "lineNo": _to_int_if_whole(item.get("lineNo")) or index + 1,
                "description": _pick(item, _LINE_ALIASES["description"]),
                "quantity": _to_int_if_whole(_pick(item, _LINE_ALIASES["quantity"])),
                "unitPrice": _to_number(_pick(item, _LINE_ALIASES["unitPrice"])),
                "lineAmount": _to_number(_pick(item, _LINE_ALIASES["lineAmount"])),
            }
        )
    return mapped


def _confidence(ocr_json: dict[str, Any], ratio: float) -> float:
    raw = ocr_json.get("confidence")
    if raw is None and isinstance(ocr_json.get("invoice_header"), dict):
        raw = ocr_json["invoice_header"].get("confidence")
    number = _to_number(raw)
    if number is None:
        return round(1.0 - ratio, 2)
    if number > 1.0:
        number = number / 100.0
    return round(max(0.0, min(1.0, number)), 2)


def _remark(remarks: Optional[dict[str, Any]], *names: str) -> Optional[str]:
    """Case/spacing-insensitive lookup: 'unique ref no' == 'Unique_Ref_No'."""
    if not isinstance(remarks, dict):
        return None
    wanted = {re.sub(r"[\s_\-]+", "", n).lower() for n in names}
    for key, value in remarks.items():
        if re.sub(r"[\s_\-]+", "", str(key)).lower() in wanted and not _is_null(value):
            return str(value).strip()
    return None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]+", "_", value).strip("._") or "document"


def _document_type(job: dict[str, Any], header: Any) -> str:
    explicit = normalize_document_type(job.get("document_type"))
    if explicit != "UNKNOWN":
        return explicit
    if isinstance(header, dict):
        return normalize_document_type(header.get("Document Type"))
    return "UNKNOWN"


def _sftp_upload(
    settings: Settings,
    remote_dir: str,
    files: list[tuple[str, bytes]],
) -> None:
    """Blocking; run in a worker thread. Creates `remote_dir` if missing."""
    import paramiko

    host = (settings.ftp_host or "").strip()
    if not host or not settings.ftp_username or not settings.ftp_password:
        raise ConnectionError("FTP server is not configured (FTP_HOST / FTP_USERNAME / FTP_PASSWORD).")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        try:
            client.connect(
                hostname=host,
                port=int(settings.ftp_sftp_port),
                username=settings.ftp_username,
                password=settings.ftp_password,
                timeout=settings.ftp_timeout_seconds,
                banner_timeout=settings.ftp_timeout_seconds,
                auth_timeout=settings.ftp_timeout_seconds,
                allow_agent=False,
                look_for_keys=False,
            )
        except Exception as exc:
            raise ConnectionError(f"Unable to connect to FTP server: {exc}") from exc

        sftp = client.open_sftp()
        try:
            path = ""
            for part in [p for p in remote_dir.split("/") if p]:
                path = f"{path}/{part}"
                try:
                    sftp.stat(path)
                except IOError:
                    sftp.mkdir(path)
            for name, data in files:
                sftp.putfo(io.BytesIO(data), posixpath.join(remote_dir, name))
        finally:
            sftp.close()
    finally:
        client.close()


class FtpAgent:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    async def _file_bytes(self, job: dict[str, Any]) -> bytes:
        if job.get("file_bytes") is not None:
            return job["file_bytes"]
        filepath = job.get("filepath")
        tenant_id = job.get("tenant_id")
        if not tenant_id:
            raise ValueError("tenantId is required to download the blob.")
        try:
            return await asyncio.to_thread(download_blob_bytes, str(tenant_id), filepath)
        except HTTPException as exc:
            raise RuntimeError(f"Blob download failed: {exc.detail}") from exc

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
        cfg = self._cfg()
        job = document_job or {}
        ocr_json = job.get("ocr_json") or {}
        remarks = job.get("remarks") or {}
        processed_at = _now()

        ratio = null_ratio(ocr_json)
        processed = ratio <= cfg.ftp_unprocessed_null_ratio
        remote_dir = cfg.ftp_processed_dir if processed else cfg.ftp_unprocessed_dir

        blob_path = job.get("filepath") or job.get("filename")
        source_name = posixpath.basename((blob_path or "").replace("\\", "/"))
        ref_no = _remark(remarks, "unique ref no", "unique_ref_no", "uniqueRefNo") or (
            posixpath.splitext(source_name)[0] if source_name else None
        ) or job.get("instance_id") or "document"
        ref_no = _safe_name(ref_no)
        extension = posixpath.splitext(source_name)[1].lower() or ".pdf"

        error: Optional[str] = None
        delivered_at: Optional[str] = None
        try:
            data = await self._file_bytes(job)
            invoice_json = json.dumps(ocr_json, ensure_ascii=False, indent=2).encode("utf-8")
            await asyncio.to_thread(
                _sftp_upload,
                cfg,
                remote_dir,
                [(f"{ref_no}{extension}", data), (f"{ref_no}.json", invoice_json)],
            )
            delivered_at = _now()
        except (ConnectionError, RuntimeError, ValueError) as exc:
            error = str(exc)
        except Exception as exc:
            logger.warning("ftp_delivery_failed", extra={"error_type": type(exc).__name__})
            error = f"SFTP upload failed: {exc}"

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
            "documentType": _document_type(job, ocr_json.get("invoice_header")),
            "processedAt": processed_at,
            "source": {
                "blobPath": blob_path,
                "fileName": _remark(remarks, "Received Filename") or source_name or None,
                "emailFrom": _remark(remarks, "From", "emailFrom"),
                "emailSubject": _remark(remarks, "Email_Subject", "emailSubject", "Subject"),
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
                "confidence": _confidence(ocr_json, ratio),
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
