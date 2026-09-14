"""HANA Cloud purchase-order connector defaults (EZOFIS tenant)."""
from __future__ import annotations

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
