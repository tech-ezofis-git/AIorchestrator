"""Build and push AP extraction metadata to V6 Core (apagentv6 port).

PATCH /Workflows/{workflowId}/instances/{instanceId}/ap-agent/metadata
Persists invoice_header + line items; does not move-next.

V6 ApplyApAgentMetadata **requires** formId + formEntryId (400 otherwise) and updates
``dbo.ezfb_{formToken}_items`` WHERE item_id = formEntryId.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.ap_skills.store import ApStoreUnavailableError

logger = logging.getLogger("orchestrator.ap.metadata")

# Code-review finding #18: every `store.*` call in this module is wrapped
# in a best-effort `except Exception` (by design — a lookup/write failure
# here degrades gracefully rather than aborting the AP run), but that
# previously logged EVERY failure at `warning`, indistinguishable from a
# genuinely unexpected bug (e.g. an AttributeError from a code defect).
# `ApStore` itself already catches its own DB errors and either returns a
# soft value or raises `ApStoreUnavailableError` — so that (an expected,
# "the store/DB is down" shape) is the only thing that should land here at
# `warning`; anything else logs louder and with a traceback.
_EXPECTED_BEST_EFFORT_ERRORS = (
    ApStoreUnavailableError,
    ConnectionError,
    TimeoutError,
    OSError,
    httpx.HTTPError,
)


def _log_best_effort_failure(event: str, exc: Exception, **extra: Any) -> None:
    if isinstance(exc, _EXPECTED_BEST_EFFORT_ERRORS):
        logger.warning(event, extra={"error_type": type(exc).__name__, **extra})
    else:
        logger.error(event, extra={"error_type": type(exc).__name__, **extra}, exc_info=True)

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


_PLACEHOLDER_VALUE_NORMS = frozenset(
    {
        "terms",
        "currency",
        "ponumber",
        "invoiceno",
        "invoicenumber",
        "vendorname",
        "vendor",
        "supplier",
        "matchedstatus",
        "documenttype",
        "invoicedate",
        "duedate",
        "invoiceamount",
        "buyer",
        "shiptoaddress",
        "invoice",
        "number",
        "na",
        "none",
        "null",
    }
)


def _stringify_header_value(value: Any, key: str = "") -> Optional[str]:
    """V6 form columns are strings; JSON numbers often land as null in ezfb."""
    if _skip_empty(value) or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    if not text:
        return None
    token = _norm_label(text)
    if key and token == _norm_label(key):
        return None
    if token in _PLACEHOLDER_VALUE_NORMS:
        return None
    return text


def _has_extracted_values(header_src: dict[str, Any]) -> bool:
    """True when extract found a real invoice/PO/vendor/amount — not form chrome."""
    checks = (
        ("invoice_number", "Invoice No", "invoice_no", "invoiceNumber"),
        ("po_number", "PO Number", "poNumber", "po"),
        ("vendor", "Vendor Name", "VENDOR Name", "Supplier", "supplier", "vendor_name"),
        ("total", "Invoice Amount", "amount", "invoice_amount"),
    )
    for keys in checks:
        value = _stringify_header_value(_first_value(header_src, *keys), keys[0])
        if value:
            return True
    return False


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
    has_real = _has_extracted_values(header_src)

    if has_real:
        for key, raw in header_src.items():
            if key in _INTERNAL_KEYS:
                continue
            value = _stringify_header_value(raw, str(key))
            if value is None:
                continue
            header[str(key)] = value

        for label, keys in _HEADER_LABELS:
            value = _stringify_header_value(_first_value(header_src, *keys), label)
            if value is None:
                continue
            header[label] = value

    if extras and has_real:
        for key, raw in extras.items():
            value = _stringify_header_value(raw, str(key))
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


def _parse_form_entry_id(raw: Any) -> Optional[str | int]:
    """GUID string for V6 uuid PK, or positive int for legacy numeric ezfb item_id."""
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _is_guid(text):
        return _hyphenate_guid(text)
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_guid(value: str) -> bool:
    compact = "".join(ch for ch in str(value or "") if ch.isalnum()).lower()
    return len(compact) == 32 and all(ch in "0123456789abcdef" for ch in compact)


def _hyphenate_guid(value: str) -> str:
    compact = "".join(ch for ch in str(value or "") if ch.isalnum()).lower()
    if len(compact) != 32 or any(ch not in "0123456789abcdef" for ch in compact):
        return (value or "").strip()
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


def _job_id(job: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = job.get(key)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        if text and text.lower() != "none":
            return text
    return ""


def merge_ids_into_job(document_job: dict[str, Any], ids: dict[str, Any]) -> dict[str, Any]:
    """Copy resolved ticket IDs onto document_job (snake_case) for later skills."""
    job = document_job if isinstance(document_job, dict) else {}
    mapping = {
        "workflow_id": ids.get("workflow_id"),
        "instance_id": ids.get("instance_id"),
        "repository_id": ids.get("repository_id"),
        "repository_item_id": ids.get("item_id") if _is_guid(str(ids.get("item_id") or "")) else ids.get("repository_item_id"),
        "form_id": ids.get("form_id"),
        "form_entry_id": ids.get("form_entry_id"),
    }
    for key, value in mapping.items():
        if value in (None, "") or job.get(key) not in (None, ""):
            continue
        job[key] = str(value)
    return job


def resolve_metadata_ids(document_job: dict[str, Any], form_id: Optional[str]) -> dict[str, Any]:
    """Resolve IDs V6 ApplyApAgentMetadata requires."""
    job = document_job or {}
    workflow_id = _job_id(job, "workflow_id", "workflowId", "WorkflowId")
    instance_id = _job_id(job, "instance_id", "instanceId", "InstanceId")
    repository_id = _job_id(job, "repository_id", "repositoryId", "RepositoryId", "repository")
    repo_item = _job_id(job, "repository_item_id", "repositoryItemId", "RepositoryItemId")
    raw_item = _job_id(job, "item_id", "itemId", "ItemId")
    form_entry_id = _parse_form_entry_id(
        job.get("form_entry_id")
        or job.get("formEntryId")
        or job.get("formentryId")
        or job.get("FormEntryId")
    )
    if form_entry_id is None:
        form_data = job.get("formData") or job.get("form_data")
        if isinstance(form_data, dict):
            form_entry_id = _parse_form_entry_id(
                form_data.get("formEntryId")
                or form_data.get("formentryId")
                or form_data.get("FormEntryId")
                or form_data.get("form_entry_id")
            )
    resolved_form_id = (
        str(form_id or _job_id(job, "form_id", "formId", "FormId", "formid") or "").strip() or None
    )

    # V6 body.itemId must be the repository item GUID (hyphenated or compact 32-hex).
    item_guid = ""
    if _is_guid(repo_item):
        item_guid = _hyphenate_guid(repo_item)
    elif _is_guid(raw_item):
        item_guid = _hyphenate_guid(raw_item)

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
    if store is not None and (not resolved_form_id or form_entry_id is None or not item_id):
        try:
            looked = await store.fetch_ticket_context(
                tenant_id=tenant_id,
                instance_id=instance_id or None,
                repository_item_id=item_id or None,
                form_id=resolved_form_id,
            )
        except Exception as exc:
            _log_best_effort_failure("ap_ticket_lookup_failed", exc)
            looked = None
        if isinstance(looked, dict) and looked:
            if not workflow_id:
                workflow_id = str(looked.get("workflow_id") or "").strip()
            if not instance_id:
                instance_id = str(looked.get("instance_id") or "").strip()
            if not repository_id:
                repository_id = str(looked.get("repository_id") or "").strip()
            if not item_id:
                item_id = str(looked.get("item_id") or "").strip()
            if not resolved_form_id:
                resolved_form_id = str(looked.get("form_id") or "").strip() or None
            if form_entry_id is None:
                form_entry_id = _parse_form_entry_id(looked.get("form_entry_id"))
            request_summary.update(
                {
                    "workflow_id": workflow_id or None,
                    "instance_id": instance_id or None,
                    "repository_id": repository_id or None,
                    "item_id": item_id or None,
                    "form_id": resolved_form_id,
                    "form_entry_id": form_entry_id,
                    "looked_up": True,
                }
            )
    if store is not None and resolved_form_id and form_entry_id is None:
        try:
            latest = await store.latest_empty_ezfb_item(
                tenant_id=tenant_id,
                form_id=resolved_form_id,
            )
        except Exception as exc:
            _log_best_effort_failure("ap_ezfb_latest_lookup_failed", exc)
            latest = None
        if latest:
            parsed_latest = _parse_form_entry_id(latest)
            if parsed_latest:
                form_entry_id = parsed_latest
                request_summary["form_entry_id"] = form_entry_id
                request_summary["form_entry_source"] = "latest_empty_row"
    if store is not None and resolved_form_id and form_entry_id is not None and header:
        try:
            ezfb_write = await store.apply_ezfb_item_fields(
                tenant_id=tenant_id,
                form_id=resolved_form_id,
                form_entry_id=form_entry_id,
                header=header,
                line_items=fields.get(_LINE_ITEM_KEY_PREFERRED) or fields.get(_LINE_ITEM_KEY_LEGACY),
                form_controls=form_controls,
            )
        except Exception as exc:
            _log_best_effort_failure("ap_ezfb_write_error", exc)
            ezfb_write = {"ok": False, "reason": type(exc).__name__}

    repo_write: Optional[dict[str, Any]] = None
    if store is not None and repository_id and header:
        repo_item = item_id if item_id and _is_guid(item_id) else ""
        if repo_item:
            repo_item = _hyphenate_guid(repo_item)
        if not repo_item:
            try:
                latest_repo = await store.latest_empty_repository_item(
                    tenant_id=tenant_id,
                    repository_id=repository_id,
                )
            except Exception as exc:
                _log_best_effort_failure("ap_repo_latest_lookup_failed", exc)
                latest_repo = None
            if latest_repo:
                repo_item = str(latest_repo).strip()
                item_id = repo_item
                request_summary["item_id"] = item_id
                request_summary["repository_item_source"] = "latest_empty_row"
        if repo_item:
            try:
                repo_write = await store.apply_repository_item_fields(
                    tenant_id=tenant_id,
                    repository_id=repository_id,
                    item_id=repo_item,
                    header=header,
                    line_items=fields.get(_LINE_ITEM_KEY_PREFERRED) or fields.get(_LINE_ITEM_KEY_LEGACY),
                    form_controls=form_controls,
                )
            except Exception as exc:
                _log_best_effort_failure("ap_repo_write_error", exc)
                repo_write = {"ok": False, "reason": type(exc).__name__}

    sql_ok = bool((ezfb_write and ezfb_write.get("ok")) or (repo_write and repo_write.get("ok")))

    can_patch = bool(workflow_id and instance_id and repository_id and item_id)
    if not can_patch:
        logger.warning("ap_metadata_skipped", extra={"reason": "missing_ids", **request_summary})
        return {
            "ok": sql_ok,
            "skipped": not sql_ok,
            "reason": "missing_ids",
            "ezfb": ezfb_write,
            "repository": repo_write,
            "request": request_summary,
        }

    if not resolved_form_id or form_entry_id is None:
        logger.warning(
            "ap_metadata_skipped",
            extra={"reason": "missing_form_ids", **request_summary},
        )
        return {
            "ok": sql_ok,
            "skipped": not sql_ok,
            "reason": "missing_form_ids",
            "ezfb": ezfb_write,
            "repository": repo_write,
            "request": request_summary,
        }

    if not fields:
        logger.warning("ap_metadata_skipped", extra={"reason": "empty_fields", **request_summary})
        return {
            "ok": False,
            "skipped": True,
            "reason": "empty_fields",
            "ezfb": ezfb_write,
            "repository": repo_write,
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
            return {
                "ok": bool(result) or sql_ok,
                "ezfb": ezfb_write,
                "repository": repo_write,
                "request": request_summary,
            }

        out = {**result, "request": request_summary, "ezfb": ezfb_write, "repository": repo_write}
        if out.get("reason") == "login_not_configured":
            out["ezfb_warning"] = "login_not_configured"
            if sql_ok:
                out["ok"] = True

        ezfb_n = out.get("ezfbFieldsUpdated")
        repo_n = out.get("repositoryFieldsUpdated")
        if out.get("ok") and not out.get("mock"):
            if ezfb_n is not None and int(ezfb_n or 0) == 0:
                logger.warning(
                    "ap_metadata_zero_ezfb_fields",
                    extra={"form_entry_id": form_entry_id, "form_id": resolved_form_id},
                )
            if repo_n is not None and int(repo_n or 0) == 0:
                logger.warning(
                    "ap_metadata_zero_repository_fields",
                    extra={"item_id": item_id, "repository_id": repository_id},
                )

        if not out.get("ok", True) and not out.get("mock"):
            logger.warning(
                "ap_metadata_push_failed",
                extra={
                    "status_code": out.get("status_code"),
                    "detail": (out.get("detail") or "")[:200],
                },
            )
            if sql_ok:
                out["ok"] = True
        return out
    except Exception as exc:
        _log_best_effort_failure("ap_metadata_push_error", exc)
        return {
            "ok": sql_ok,
            "error_type": type(exc).__name__,
            "ezfb": ezfb_write,
            "repository": repo_write,
            "request": request_summary,
        }
