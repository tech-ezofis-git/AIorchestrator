"""PO master helpers: Workflow payload only (no tenant GUID hardcodes)."""
from __future__ import annotations

from typing import Any, Optional

from app.ap_skills.types import field_number, field_text

# Known HANA Cloud connector GUID (detection only — never used as a silent default).
# Prefer payload.resource containing HANA or po_lookup_sap artifact source=hana.
HANA_PO_CONNECTOR_ID = "f7636e21-1a0c-457c-a2b4-e28430705477"

# Legacy constant kept for tests that still reference the demo tenant id.
# Inject / connector resolution must NOT gate on this.
EZOFIS_TENANT_ID = "b843b988-00ec-44e3-aca2-b8470133ef63"


def _norm_guid(value: str) -> str:
    return (value or "").replace("-", "").strip().lower()


def is_ezofis_tenant(tenant_id: str) -> bool:
    """Deprecated: do not use for PO master routing. Prefer Workflow payload."""
    return _norm_guid(tenant_id) == _norm_guid(EZOFIS_TENANT_ID)


def is_hana_po_connector(connector_id: str) -> bool:
    """True when connector_id matches the known HANA Cloud connector GUID."""
    return bool(connector_id) and _norm_guid(connector_id) == _norm_guid(HANA_PO_CONNECTOR_ID)


def resolve_connector_id(*, connector_id: str, tenant_id: str = "") -> str:
    """Return payload/threshold connector_id only — never invent a tenant default.

    ``tenant_id`` is accepted for call-site compatibility and ignored.
    """
    _ = tenant_id
    return (connector_id or "").strip()


def resolve_hana_connector_id(*, tenant_id: str, connector_id: str) -> str:
    """Alias of ``resolve_connector_id`` (no EZOFIS silent default)."""
    return resolve_connector_id(tenant_id=tenant_id, connector_id=connector_id)


_FORM_MASTER_TOKENS = frozenset(
    {
        "INTERNALFORM",
        "INTERNAL_FORM",
        "EZOFIS",
        "FORM",
        "EZFB",
        "MASTERFORM",
        "POMASTERFORM",
    }
)
_OTHER_CONNECTOR_RESOURCES = frozenset({"QUICKBOOKS", "QB", "SAGE"})


def _norm_master_token(value: str) -> str:
    return (
        (value or "")
        .strip()
        .upper()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def wants_sap_or_hana_po_master(
    document_job: Optional[dict[str, Any]] = None,
    *,
    thresholds: Optional[dict[str, Any]] = None,
) -> bool:
    """True when Workflow/payload asks for SAP or HANA Cloud PO master.

    Core Phase 3: ``master_source=InternalForm`` (+ ``master_form_id``) → form.
    SAP/HANA sets ``resource`` + ``connector_id`` (+ skills). Empty → InternalForm.
    """
    job = document_job if isinstance(document_job, dict) else {}
    thresholds = thresholds if isinstance(thresholds, dict) else {}

    master_raw = str(
        job.get("master_source")
        or job.get("masterSource")
        or job.get("MasterSource")
        or ""
    ).strip()
    if master_raw:
        master = _norm_master_token(master_raw)
        if master in _FORM_MASTER_TOKENS or "INTERNALFORM" in master:
            return False
        if master in {"QUICKBOOKS", "QB", "SAGE"}:
            return False
        if "HANA" in master or master.startswith("SAP") or master in {"S4", "S4HANA"}:
            return True
        return False

    resource = str(
        job.get("resource") or thresholds.get("po_resource") or ""
    ).strip().upper()
    if resource:
        compact = resource.replace(" ", "_").replace("-", "_")
        slashless = compact.replace("/", "")
        formish = _norm_master_token(resource)
        if formish in _FORM_MASTER_TOKENS or compact in _OTHER_CONNECTOR_RESOURCES:
            return False
        if "HANA" in resource:
            return True
        if (
            compact.startswith("SAP")
            or slashless.startswith("SAP")
            or compact in {"S4", "S/4"}
            or slashless in {"S4", "S4HANA"}
        ):
            return True
        return False

    # No Workflow masterSource / resource → InternalForm (ezfb PO master).
    return False


def wants_form_po_master(
    document_job: Optional[dict[str, Any]] = None,
    *,
    thresholds: Optional[dict[str, Any]] = None,
) -> bool:
    """True when PO should come from ezfb /masters/po (not a connector skill)."""
    job = document_job if isinstance(document_job, dict) else {}
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    if wants_sap_or_hana_po_master(job, thresholds=thresholds):
        return False
    resource = str(job.get("resource") or thresholds.get("po_resource") or "").strip().upper()
    compact = resource.replace(" ", "_").replace("-", "_")
    if compact in _OTHER_CONNECTOR_RESOURCES:
        return False
    master_raw = str(
        job.get("master_source") or job.get("masterSource") or job.get("MasterSource") or ""
    ).strip()
    if master_raw:
        master = _norm_master_token(master_raw)
        if master in {"QUICKBOOKS", "QB", "SAGE"}:
            return False
    return True


# Core POST …/hana/purchase-orders/match item shape (camelCase).
_HANA_MATCH_ITEM_KEYS: tuple[tuple[str, str], ...] = (
    ("itemNumber", "item_number"),
    ("itemCategory", "item_category"),
    ("materialId", "material_id"),
    ("materialDescription", "material_description"),
    ("materialGroup", "material_group"),
    ("plant", "plant"),
    ("orderQuantity", "order_quantity"),
    ("unitOfMeasure", "unit_of_measure"),
    ("netPrice", "net_price"),
    ("priceUnit", "price_unit"),
    ("netValue", "net_value"),
)


def hana_match_item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map one HANA PO line to the match API ``items[]`` entry."""
    out: dict[str, Any] = {}
    for camel, snake in _HANA_MATCH_ITEM_KEYS:
        val = row.get(camel)
        if val is None:
            val = row.get(snake)
        if val is not None and val != "":
            out[camel] = val
    return out


def _match_items_from_po(po: dict[str, Any]) -> list[dict[str, Any]]:
    raw = po.get("match_items")
    if isinstance(raw, list) and raw:
        return [row for row in raw if isinstance(row, dict) and row]
    items: list[dict[str, Any]] = []
    for line in po.get("lines") or []:
        if not isinstance(line, dict):
            continue
        row: dict[str, Any] = {}
        line_no = line.get("line_no")
        if line_no is not None:
            row["itemNumber"] = line_no
        for src, dst in (
            ("description", "materialDescription"),
            ("qty", "orderQuantity"),
            ("unit_price", "netPrice"),
            ("amount", "netValue"),
        ):
            if line.get(src) is not None:
                row[dst] = line[src]
        if row:
            items.append(row)
    return items


def build_hana_po_invoice_match_body(
    *,
    instance_id: str,
    po_number: str,
    invoice_number: str,
    status: str,
    invoice: dict[str, Any],
    po: Optional[dict[str, Any]] = None,
    invoice_status: str = "Follow-On Documents",
) -> dict[str, Any]:
    """Body for ``POST /connector/{id}/hana/purchase-orders/match``."""
    po = po or {}
    supplier = field_text(invoice, "vendor", "supplier_name", "supplierName", "supplier")
    if not supplier:
        supplier = str(po.get("vendor") or po.get("supplier_id") or "").strip()
    currency = field_text(invoice, "currency") or str(po.get("currency") or "").strip()
    total = field_number(invoice, "total", "total_amount", "totalAmount")
    if total is None and po.get("total") is not None:
        try:
            total = float(po["total"])
        except (TypeError, ValueError):
            total = None
    invoice_date = field_text(invoice, "invoice_date", "invoiceDate")
    body: dict[str, Any] = {
        "instanceId": instance_id,
        "poNumber": po_number,
        "invoiceNumber": invoice_number,
        "supplierName": supplier,
        "invoiceDate": invoice_date,
        "currency": currency,
        "totalAmount": total,
        "status": status,
        "invoiceStatus": invoice_status,
        "items": _match_items_from_po(po),
    }
    return {k: v for k, v in body.items() if v is not None and v != ""}
