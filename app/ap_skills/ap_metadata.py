"""Build and push AP extraction metadata to V6 Core (apagentv6 port).

PATCH /Workflows/{workflowId}/instances/{instanceId}/ap-agent/metadata
Persists invoice_header + line items; does not move-next.

V6 ApplyApAgentMetadata **requires** formId + formEntryId (400 otherwise) and updates
``dbo.ezfb_{formToken}_items`` WHERE item_id = formEntryId.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("orchestrator.ap.metadata")

# (output label, source keys). Duplicate labels for aliasing across form styles.
_HEADER_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PO Number", ("po_number", "PO Number", "poNumber")),
    ("Invoice No", ("invoice_number", "Invoice No", "invoice_no", "invoiceNumber")),
    ("Invoice Amount", ("total", "Invoice Amount", "amount", "invoice_amount")),
    ("Supplier", ("vendor", "Supplier", "supplier", "vendor_name", "Supplier Name", "Vendor Name", "VENDOR Name")),
    ("Vendor Name", ("vendor", "Vendor Name", "VENDOR Name", "vendor_name", "Supplier", "Supplier Name", "supplier")),
    ("Supplier Address", ("supplier_address", "Supplier Address", "vendor_address", "Vendor Address")),
    ("Vendor Address", ("vendor_address", "Vendor Address", "supplier_address", "Supplier Address")),
    ("Ship To Address", ("ship_to_address", "Ship To Address", "ship_to")),
    ("PO Date", ("po_date", "PO Date", "PO DATE")),
    ("PO DATE", ("po_date", "PO DATE", "PO Date")),
    ("Due Date", ("due_date", "Due Date")),
    ("Invoice Date", ("invoice_date", "Invoice Date")),
    ("Currency", ("currency", "Currency")),
    ("Terms", ("terms", "Terms", "TERMS")),
    ("TERMS", ("terms", "TERMS", "Terms")),
    ("Buyer", ("buyer", "Buyer")),
    ("Invoice Tax Amount", ("tax", "Invoice Tax Amount", "tax_amount", "invoice_tax_amount")),
    ("Matched Status", ("matched_status", "Matched Status")),
    ("Document Type", ("document_type", "Document Type", "doc_type")),
)

_LINE_ITEM_KEY_PREFERRED = "Invoice Extracted Line Item"
_LINE_ITEM_KEY_LEGACY = "Line Item"
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key not in data:
            continue
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _skip_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def build_ap_metadata_fields(invoice: dict[str, Any]) -> dict[str, Any]:
    """Map extract_invoice / OCR payload → V6 metadata ``fields`` object."""
    if not isinstance(invoice, dict) or not invoice:
        return {}

    header_src = invoice.get("invoice_header")
    if not isinstance(header_src, dict):
        header_src = invoice

    header: dict[str, Any] = {}
    for label, keys in _HEADER_LABELS:
        value = _first_value(header_src, *keys)
        if _skip_empty(value):
            continue
        header[label] = value if not isinstance(value, str) else value.strip()

    lines_raw = (
        invoice.get(_LINE_ITEM_KEY_PREFERRED)
        or invoice.get(_LINE_ITEM_KEY_LEGACY)
        or invoice.get("line_items")
        or invoice.get("lines")
        or []
    )
    lines: list[dict[str, Any]] = []
    if isinstance(lines_raw, list):
        for idx, line in enumerate(lines_raw, start=1):
            if not isinstance(line, dict):
                continue
            mapped = {
                "line_no": _first_value(line, "line_no", "line", "Line") or idx,
                "item_no": _first_value(line, "item_no", "part_number", "Part Number", "sku"),
                "description": _first_value(line, "description", "item", "name", "Description"),
                "quantity": _first_value(line, "quantity", "qty", "Quantity"),
                "uom": _first_value(line, "uom", "UOM"),
                "rate": _first_value(line, "rate", "unit_cost", "Unit Cost"),
                "price": _first_value(line, "price", "unit_price"),
                "line_amount": _first_value(line, "line_amount", "amount", "Extended"),
            }
            cleaned = {k: v for k, v in mapped.items() if not _skip_empty(v)}
            if cleaned:
                lines.append(cleaned)

    fields: dict[str, Any] = {}
    if header:
        fields["invoice_header"] = header
    if lines:
        fields[_LINE_ITEM_KEY_PREFERRED] = lines
        fields[_LINE_ITEM_KEY_LEGACY] = lines
    return fields


def _parse_form_entry_id(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_guid(value: str) -> bool:
    return bool(_GUID_RE.match((value or "").strip()))


def resolve_metadata_ids(document_job: dict[str, Any], form_id: Optional[str]) -> dict[str, Any]:
    """Resolve IDs V6 ApplyApAgentMetadata requires."""
    job = document_job or {}
    workflow_id = str(job.get("workflow_id") or "").strip()
    instance_id = str(job.get("instance_id") or "").strip()
    repository_id = str(job.get("repository_id") or "").strip()
    repo_item = str(job.get("repository_item_id") or "").strip()
    raw_item = str(job.get("item_id") or "").strip()
    form_entry_id = _parse_form_entry_id(job.get("form_entry_id"))
    resolved_form_id = str(form_id or job.get("form_id") or "").strip() or None

    # V6 body.itemId must be the repository item GUID.
    item_guid = ""
    if _is_guid(repo_item):
        item_guid = repo_item
    elif _is_guid(raw_item):
        item_guid = raw_item

    # If formentryId was omitted but item_id is a positive int, treat it as form entry PK.
    if form_entry_id is None and raw_item and not _is_guid(raw_item):
        form_entry_id = _parse_form_entry_id(raw_item)

    return {
        "workflow_id": workflow_id,
        "instance_id": instance_id,
        "repository_id": repository_id,
        "item_id": item_guid,
        "form_id": resolved_form_id,
        "form_entry_id": form_entry_id,
    }


async def push_extract_metadata(
    *,
    ezofis: Any,
    tenant_id: str,
    document_job: dict[str, Any],
    form_id: Optional[str],
    invoice: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort metadata PATCH after extract_invoice (non-fatal upstream)."""
    ids = resolve_metadata_ids(document_job, form_id)
    workflow_id = ids["workflow_id"]
    instance_id = ids["instance_id"]
    repository_id = ids["repository_id"]
    item_id = ids["item_id"]
    form_entry_id = ids["form_entry_id"]
    resolved_form_id = ids["form_id"]

    fields = build_ap_metadata_fields(invoice)
    request_summary = {
        "workflow_id": workflow_id or None,
        "instance_id": instance_id or None,
        "repository_id": repository_id or None,
        "item_id": item_id or None,
        "form_id": resolved_form_id,
        "form_entry_id": form_entry_id,
        "header_keys": sorted((fields.get("invoice_header") or {}).keys()),
        "line_item_count": len(fields.get(_LINE_ITEM_KEY_PREFERRED) or []),
    }

    if not workflow_id or not instance_id or not repository_id or not item_id:
        logger.warning("ap_metadata_skipped", extra={"reason": "missing_ids", **request_summary})
        return {"ok": False, "skipped": True, "reason": "missing_ids", "request": request_summary}

    # V6 controller requires formId + formEntryId; without them PATCH is 400 and ezfb is untouched.
    if not resolved_form_id or form_entry_id is None:
        logger.warning(
            "ap_metadata_skipped",
            extra={"reason": "missing_form_ids", **request_summary},
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": "missing_form_ids",
            "request": request_summary,
        }

    if not fields:
        logger.warning("ap_metadata_skipped", extra={"reason": "empty_fields", **request_summary})
        return {"ok": False, "skipped": True, "reason": "empty_fields", "request": request_summary}

    try:
        result = await ezofis.apply_ap_agent_metadata(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            instance_id=instance_id,
            repository_id=repository_id,
            item_id=item_id,
            fields=fields,
            form_id=resolved_form_id,
            form_entry_id=form_entry_id,
        )
        if not isinstance(result, dict):
            return {"ok": bool(result), "request": request_summary}

        out = {**result, "request": request_summary}
        if out.get("reason") == "login_not_configured":
            out["ezfb_warning"] = "login_not_configured"

        ezfb_n = out.get("ezfbFieldsUpdated")
        if out.get("ok") and not out.get("mock") and ezfb_n is not None and int(ezfb_n or 0) == 0:
            logger.warning(
                "ap_metadata_zero_ezfb_fields",
                extra={"form_entry_id": form_entry_id, "form_id": resolved_form_id},
            )

        if not out.get("ok", True) and not out.get("mock"):
            logger.warning(
                "ap_metadata_push_failed",
                extra={
                    "status_code": out.get("status_code"),
                    "detail": (out.get("detail") or "")[:200],
                },
            )
        return out
    except Exception as exc:
        logger.warning(
            "ap_metadata_push_error",
            extra={"error_type": type(exc).__name__},
        )
        return {"ok": False, "error_type": type(exc).__name__, "request": request_summary}
