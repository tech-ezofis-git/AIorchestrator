"""HANA Cloud purchase-order connector defaults (EZOFIS tenant)."""
from __future__ import annotations

from typing import Any, Optional

from app.ap_skills.types import field_number, field_text

# From HANA_Cloud_Purchase_Order_API.docx
EZOFIS_TENANT_ID = "b843b988-00ec-44e3-aca2-b8470133ef63"
HANA_PO_CONNECTOR_ID = "f7636e21-1a0c-457c-a2b4-e28430705477"


def _norm_guid(value: str) -> str:
    return (value or "").replace("-", "").strip().lower()


def is_ezofis_tenant(tenant_id: str) -> bool:
    return _norm_guid(tenant_id) == _norm_guid(EZOFIS_TENANT_ID)


def is_hana_po_connector(connector_id: str) -> bool:
    return _norm_guid(connector_id) == _norm_guid(HANA_PO_CONNECTOR_ID)


def resolve_hana_connector_id(*, tenant_id: str, connector_id: str) -> str:
    """Default HANA PO connector for EZOFIS when none is passed."""
    cid = (connector_id or "").strip()
    if cid:
        return cid
    if is_ezofis_tenant(tenant_id):
        return HANA_PO_CONNECTOR_ID
    return ""


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
    invoice_status: str = "Open",
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
