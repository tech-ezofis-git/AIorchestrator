"""File Preparation tool — renames the document and the invoice JSON to the unique ref.

Produces up to two files: `{ref}{ext}` (the document, when given) and `{ref}.json`.
The JSON is the FTP contract shape when valid, the extraction as received when not.
"""
from __future__ import annotations

import json
import mimetypes
import posixpath
from typing import Any, Optional

from app.classification_skills.contract import numeric_id
from app.tools.invoice_mapping import (
    FTP_AGENT_NAME,
    document_type as _document_type,
    extraction_confidence,
    map_invoice_header,
    map_line_items,
    now_iso,
    remark as _remark,
    safe_name,
)
from app.tools.invoice_scorer import invoice_parts

TOOL_ID = "file_preparation"
DEFAULT_EXTENSION = ".pdf"


def unique_ref(
    *,
    remarks: Optional[dict[str, Any]] = None,
    source_name: Optional[str] = None,
    instance_id: Optional[str] = None,
) -> str:
    """remarks "unique ref no", else the source file name without extension, else instance id."""
    name = posixpath.basename((source_name or "").replace("\\", "/"))
    ref = (
        _remark(remarks, "unique ref no", "unique_ref_no", "uniqueRefNo")
        or (posixpath.splitext(name)[0] if name else None)
        or instance_id
        or "document"
    )
    return safe_name(str(ref))


def invoice_contract(ocr_json: dict[str, Any], job: dict[str, Any], null_ratio: float) -> dict[str, Any]:
    """FTP contract shape: source / environment / extraction with mapped header + line items."""
    header, items = invoice_parts(ocr_json)
    extraction = ocr_json.get("extraction") if isinstance(ocr_json.get("extraction"), dict) else {}
    flat = {
        "invoice_header": dict(header),
        "line_items": items,
        "confidence": ocr_json.get("confidence", extraction.get("confidence")),
    }
    remarks = job.get("remarks")
    blob_path = job.get("filepath")
    source_name = posixpath.basename((blob_path or job.get("filename") or "").replace("\\", "/"))
    return {
        "agent": FTP_AGENT_NAME,
        "documentType": _document_type(job, header),
        "processedAt": now_iso(),
        "source": {
            "blobPath": blob_path or job.get("filename"),
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
            "confidence": extraction_confidence(flat, null_ratio),
            "invoiceHeader": map_invoice_header(header),
            "lineItems": map_line_items(items),
        },
    }


def _extension(source_name: str, content_type: Optional[str]) -> str:
    ext = posixpath.splitext(source_name)[1].lower()
    if ext:
        return ext
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) if content_type else None
    return guessed or DEFAULT_EXTENSION


def prepare_files(
    ocr_json: Optional[dict[str, Any]],
    *,
    valid: bool,
    null_ratio: float = 0.0,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    remarks: Optional[dict[str, Any]] = None,
    filepath: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    env_type: Optional[str] = None,
    document_type: Optional[str] = None,
    model_display: Optional[str] = None,
) -> dict[str, Any]:
    """Returns {tool, status, valid, unique_ref, files, json, error}. Never raises.

    A missing/empty extraction is written as `{}` (it scores as not valid).

    `files` is [{name, content_type, size, data}] — `data` holds the bytes for the
    Folder Mover; strip it before returning over HTTP.
    """
    ocr_json = ocr_json if isinstance(ocr_json, dict) else {}
    source_name = posixpath.basename((filepath or filename or "").replace("\\", "/"))
    ref = unique_ref(remarks=remarks, source_name=source_name, instance_id=instance_id)
    job = {
        "remarks": remarks,
        "filepath": filepath,
        "filename": filename,
        "tenant_id": tenant_id,
        "workflow_id": workflow_id,
        "repository_id": repository_id,
        "instance_id": instance_id,
        "env_type": env_type,
        "document_type": document_type,
        "model_display": model_display,
    }
    content = invoice_contract(ocr_json, job, null_ratio) if valid else ocr_json
    json_bytes = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")

    files: list[dict[str, Any]] = []
    if file_bytes:
        doc_type = content_type or mimetypes.guess_type(source_name)[0] or "application/octet-stream"
        files.append(
            {
                "name": f"{ref}{_extension(source_name, content_type)}",
                "content_type": doc_type,
                "size": len(file_bytes),
                "data": file_bytes,
            }
        )
    files.append({"name": f"{ref}.json", "content_type": "application/json", "size": len(json_bytes), "data": json_bytes})
    return {
        "tool": TOOL_ID,
        "status": "SUCCEEDED",
        "valid": valid,
        "unique_ref": ref,
        "files": files,
        "json": content,
        "error": None,
    }
