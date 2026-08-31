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
_HEADER_WRAPPERS = (
    "invoice_header",
    "invoiceHeader",
    "header",
    "po_row",
    "Extracted Invoice JSON",
)
_LINE_KEYS = (
    "Invoice Extracted Line Item",
    "Line Item",
    "Line Items",
    "line_items",
    "lines",
)
_INTERNAL_KEYS = frozenset(
    {
        *_HEADER_WRAPPERS,
        *_LINE_KEYS,
        "output",
        "tokens",
        "invoice",
        "metadata_push",
        "ocr_text",
        "source",
        "ocr_mock",
    }
)
_MATCH_LABELS = {
    "MATCHED": "Matched",
    "PARTIALLY_MATCHED": "Partially Matched",
    "NOT_MATCHED": "Not Matched",
    "NON_INVOICE": "Non-Invoice",
    "DUPLICATE": "Not Matched",
}
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


def _stringify_header_value(value: Any) -> Optional[str]:
    """V6 form columns are strings; JSON numbers often land as null in ezfb."""
    if _skip_empty(value) or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def _unwrap_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(invoice, dict):
        return {}
    for key in ("output", "invoice"):
        inner = invoice.get(key)
        if isinstance(inner, dict) and (
            inner.get("invoice_header") or inner.get("line_items") or inner.get("Line Item")
        ):
            return inner
    return invoice


def _header_source(invoice: dict[str, Any]) -> dict[str, Any]:
    invoice = _unwrap_invoice(invoice)
    merged: dict[str, Any] = {}
    for key in _HEADER_WRAPPERS:
        header = invoice.get(key)
        if isinstance(header, dict) and header:
            merged.update(header)
            break
    for key, value in invoice.items():
        if key in _INTERNAL_KEYS:
            continue
        merged[key] = value
    return merged


def _norm_label(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _underscore_label(value: str) -> str:
    text = str(value or "").strip()
    if not text or " " not in text:
        return ""
    return "_".join(text.split())


def apply_form_control_aliases(
    header: dict[str, Any],
    form_controls: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Copy each value onto wFormControl name, columnName, and jsonId so V6 can map ezfb columns."""
    if not header:
        return header
    expanded = dict(header)
    for key, value in list(header.items()):
        underscored = _underscore_label(key)
        if underscored and underscored not in expanded:
            expanded[underscored] = value
    if not form_controls:
        return expanded
    by_norm: dict[str, Any] = {}
    for key, value in expanded.items():
        norm = _norm_label(key)
        if norm and norm not in by_norm:
            by_norm[norm] = value
    for control in form_controls:
        if not isinstance(control, dict):
            continue
        names = [
            str(control.get("name") or "").strip(),
            str(control.get("column_name") or control.get("columnName") or "").strip(),
            str(control.get("json_id") or control.get("jsonId") or "").strip(),
        ]
        value = None
        for name in names:
            if not name:
                continue
            value = expanded.get(name)
            if value is not None:
                break
            value = by_norm.get(_norm_label(name))
            if value is not None:
                break
        if value is None:
            continue
        for name in names:
            if name:
                expanded[name] = value
        display = names[0]
        underscored = _underscore_label(display)
        if underscored:
            expanded[underscored] = value
    return expanded


def extras_from_artifacts(artifacts: dict[str, Any], skill_id: str) -> dict[str, Any]:
    """Header overlays from later AP skills (Matched Status, vendor, …)."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    extras: dict[str, Any] = {}
    finalize = artifacts.get("finalize_decision") if isinstance(artifacts.get("finalize_decision"), dict) else {}
    duplicate = artifacts.get("duplicate_detect") if isinstance(artifacts.get("duplicate_detect"), dict) else {}
    po_match = artifacts.get("po_match") if isinstance(artifacts.get("po_match"), dict) else {}
    gl_match = artifacts.get("gl_match") if isinstance(artifacts.get("gl_match"), dict) else {}
    grn_match = artifacts.get("grn_match") if isinstance(artifacts.get("grn_match"), dict) else {}
    vendor = artifacts.get("vendor_validate") if isinstance(artifacts.get("vendor_validate"), dict) else {}
    current = artifacts.get(skill_id) if isinstance(artifacts.get(skill_id), dict) else {}

    decision = (
        finalize.get("decision")
        or current.get("decision")
        or po_match.get("decision")
        or gl_match.get("decision")
        or grn_match.get("decision")
    )
    if duplicate.get("is_duplicate_invoice") and not finalize.get("decision"):
        extras["Matched Status"] = "Not Matched"
    elif decision:
        extras["Matched Status"] = _MATCH_LABELS.get(str(decision).strip().upper(), str(decision).strip())

    vendor_name = vendor.get("vendor") or vendor.get("expected")
    if vendor_name:
        extras["Supplier"] = vendor_name
        extras["Vendor Name"] = vendor_name
    return extras


def build_ap_metadata_fields(
    invoice: dict[str, Any],
    *,
    extras: Optional[dict[str, Any]] = None,
    form_controls: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Map extract_invoice / OCR payload → V6 metadata ``fields`` object.

    Keeps original form labels (so wFormControl Label/jsonId/columnName match)
    and overlays canonical aliases. Empty values are omitted — never sent as null.
    """
    if not isinstance(invoice, dict) or not invoice:
        invoice = {}

    header_src = _header_source(invoice)
    header: dict[str, Any] = {}

    for key, raw in header_src.items():
        if key in _INTERNAL_KEYS:
            continue
        value = _stringify_header_value(raw)
        if value is None:
            continue
        header[str(key)] = value

    for label, keys in _HEADER_LABELS:
        value = _stringify_header_value(_first_value(header_src, *keys))
        if value is None:
            continue
        header[label] = value

    if extras:
        for key, raw in extras.items():
            value = _stringify_header_value(raw)
            if value is None:
                continue
            header[str(key)] = value

    header = apply_form_control_aliases(header, form_controls)

    invoice = _unwrap_invoice(invoice)
    lines_raw: list[Any] = []
    for key in _LINE_KEYS:
        candidate = invoice.get(key)
        if isinstance(candidate, list) and candidate:
            lines_raw = candidate
            break

    lines: list[dict[str, Any]] = []
    if isinstance(lines_raw, list):
        for idx, line in enumerate(lines_raw, start=1):
            if not isinstance(line, dict):
                continue
            mapped: dict[str, Any] = {}
            for key, raw in line.items():
                if _skip_empty(raw) or isinstance(raw, (dict, list)):
                    continue
                mapped[str(key)] = raw
            aliases = {
                "line_no": _first_value(line, "line_no", "line", "Line") or idx,
                "item_no": _first_value(line, "item_no", "part_number", "Part Number", "sku"),
                "description": _first_value(line, "description", "item", "name", "Description"),
                "quantity": _first_value(line, "quantity", "qty", "Quantity"),
                "uom": _first_value(line, "uom", "UOM"),
                "rate": _first_value(line, "rate", "unit_cost", "Unit Cost"),
                "price": _first_value(line, "price", "unit_price"),
                "line_amount": _first_value(line, "line_amount", "amount", "Extended"),
            }
            for key, raw in aliases.items():
                if _skip_empty(raw):
                    continue
                mapped[key] = raw
            if mapped:
                lines.append(mapped)

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
    if form_entry_id is None:
        form_data = job.get("formData") or job.get("form_data")
        if isinstance(form_data, dict):
            form_entry_id = _parse_form_entry_id(
                form_data.get("formEntryId") or form_data.get("formentryId") or form_data.get("form_entry_id")
            )
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
    extras: Optional[dict[str, Any]] = None,
    skill_id: Optional[str] = None,
    form_controls: Optional[list[dict[str, Any]]] = None,
    store: Any = None,
) -> dict[str, Any]:
    """Best-effort metadata PATCH after each AP skill (non-fatal upstream)."""
    ids = resolve_metadata_ids(document_job, form_id)
    workflow_id = ids["workflow_id"]
    instance_id = ids["instance_id"]
    repository_id = ids["repository_id"]
    item_id = ids["item_id"]
    form_entry_id = ids["form_entry_id"]
    resolved_form_id = ids["form_id"]

    fields = build_ap_metadata_fields(invoice, extras=extras, form_controls=form_controls)
    request_summary = {
        "skill_id": skill_id,
        "workflow_id": workflow_id or None,
        "instance_id": instance_id or None,
        "repository_id": repository_id or None,
        "item_id": item_id or None,
        "form_id": resolved_form_id,
        "form_entry_id": form_entry_id,
        "header_keys": sorted((fields.get("invoice_header") or {}).keys()),
        "line_item_count": len(fields.get(_LINE_ITEM_KEY_PREFERRED) or []),
    }

    ezfb_write: Optional[dict[str, Any]] = None
    header = (fields.get("invoice_header") or {}) if isinstance(fields, dict) else {}
    if store is not None and resolved_form_id and form_entry_id is not None and header:
        try:
            ezfb_write = await store.apply_ezfb_item_fields(
                tenant_id=tenant_id,
                form_id=resolved_form_id,
                form_entry_id=int(form_entry_id),
                header=header,
                line_items=fields.get(_LINE_ITEM_KEY_PREFERRED) or fields.get(_LINE_ITEM_KEY_LEGACY),
                form_controls=form_controls,
            )
        except Exception as exc:
            logger.warning("ap_ezfb_write_error", extra={"error_type": type(exc).__name__})
            ezfb_write = {"ok": False, "reason": type(exc).__name__}

    can_patch = bool(workflow_id and instance_id and repository_id and item_id)
    if not can_patch:
        logger.warning("ap_metadata_skipped", extra={"reason": "missing_ids", **request_summary})
        if ezfb_write and ezfb_write.get("ok"):
            return {
                "ok": True,
                "skipped": False,
                "reason": "missing_ids",
                "ezfb": ezfb_write,
                "request": request_summary,
            }
        return {"ok": False, "skipped": True, "reason": "missing_ids", "ezfb": ezfb_write, "request": request_summary}

    if not resolved_form_id or form_entry_id is None:
        logger.warning(
            "ap_metadata_skipped",
            extra={"reason": "missing_form_ids", **request_summary},
        )
        return {
            "ok": bool(ezfb_write and ezfb_write.get("ok")),
            "skipped": True,
            "reason": "missing_form_ids",
            "ezfb": ezfb_write,
            "request": request_summary,
        }

    if not fields:
        logger.warning("ap_metadata_skipped", extra={"reason": "empty_fields", **request_summary})
        return {
            "ok": False,
            "skipped": True,
            "reason": "empty_fields",
            "ezfb": ezfb_write,
            "request": request_summary,
        }

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
            return {"ok": bool(result), "ezfb": ezfb_write, "request": request_summary}

        out = {**result, "request": request_summary, "ezfb": ezfb_write}
        if out.get("reason") == "login_not_configured":
            out["ezfb_warning"] = "login_not_configured"
            if ezfb_write and ezfb_write.get("ok"):
                out["ok"] = True

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
            if ezfb_write and ezfb_write.get("ok"):
                out["ok"] = True
        return out
    except Exception as exc:
        logger.warning(
            "ap_metadata_push_error",
            extra={"error_type": type(exc).__name__},
        )
        return {
            "ok": bool(ezfb_write and ezfb_write.get("ok")),
            "error_type": type(exc).__name__,
            "ezfb": ezfb_write,
            "request": request_summary,
        }
