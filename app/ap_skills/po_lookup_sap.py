"""po_lookup_sap — PO from SAP or HANA Cloud connector when resource=SAP/HANA."""
from __future__ import annotations

from typing import Any

from app.ap_skills.hana_po import is_hana_po_connector, resolve_hana_connector_id
from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_text, invoice_from

SKILL_ID = "po_lookup_sap"

# Workflow / payload resource labels that mean "use SAP PO master".
_SAP_RESOURCES = frozenset(
    {
        "SAP",
        "S4",
        "S/4",
        "SAP_ECC",
        "SAPECC",
        "SAP_S4",
        "SAPS4",
        "SAP_XSUAA",
        "SAPXSUAA",
        "SAP_BTP",
        "SAPBTP",
    }
)


def _is_sap_resource(resource: str) -> bool:
    raw = (resource or "").strip().upper()
    if not raw:
        return True  # unset resource → allow (caller opted into this skill)
    compact = raw.replace(" ", "_").replace("-", "_")
    slashless = compact.replace("/", "")
    if compact in _SAP_RESOURCES or slashless in _SAP_RESOURCES:
        return True
    if "HANA" in raw:
        return True
    # SAP ECC, SAP S/4 HANA, etc.
    return compact.startswith("SAP") or slashless.startswith("SAP")


def _use_hana_lookup(*, connector_id: str, resource: str) -> bool:
    if is_hana_po_connector(connector_id):
        return True
    raw = (resource or "").strip().upper()
    return "HANA" in raw


def _result(
    *,
    po_number: str | None,
    po: dict[str, Any] | None,
    reason: str,
    connector_id: str | None = None,
    skipped: bool = False,
    lookup_error: str | None = None,
    backend: str = "sap",
) -> ApSkillResult:
    data: dict[str, Any] = {
        "po_number": po_number,
        "po": po,
        "source": backend,
        "reason": reason,
    }
    if connector_id:
        data["connector_id"] = connector_id
    if skipped:
        data["skipped"] = True
    if lookup_error:
        data["lookup_error"] = lookup_error
    return ApSkillResult(skill_id=SKILL_ID, data=data)


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    if not po_number:
        return _result(
            po_number=None,
            po=None,
            reason="Invoice has no PO number for SAP lookup.",
        )

    job = ctx.document_job or {}
    connector_id = resolve_hana_connector_id(
        tenant_id=ctx.tenant_id,
        connector_id=str(
            job.get("connector_id")
            or ctx.thresholds.get("sap_connector_id")
            or ""
        ).strip(),
    )
    resource = str(job.get("resource") or ctx.thresholds.get("po_resource") or "").strip().upper()
    if resource and not _is_sap_resource(resource):
        return _result(
            po_number=po_number,
            po=None,
            reason=f"Resource is {resource}, not SAP/HANA — purchase order lookup skipped.",
            skipped=True,
        )

    live = hasattr(ctx.ezofis, "_live_enabled") and ctx.ezofis._live_enabled()
    if not connector_id and live:
        raise ApSkillError(
            "po_lookup_sap requires payload.connector_id (SAP/HANA connector GUID) when live Ezofis is enabled."
        )

    use_hana = _use_hana_lookup(connector_id=connector_id, resource=resource)
    backend = "hana" if use_hana else "sap"
    if use_hana:
        po = await ctx.ezofis.lookup_po_hana(
            tenant_id=ctx.tenant_id,
            po_number=po_number,
            connector_id=connector_id or "mock",
        )
    else:
        po = await ctx.ezofis.lookup_po_sap(
            tenant_id=ctx.tenant_id,
            po_number=po_number,
            connector_id=connector_id or "mock",
        )

    if isinstance(po, dict) and po.get("lookup_error"):
        err = str(po.get("lookup_error") or "request_failed")
        detail = str(po.get("reason") or "").strip()
        label = "HANA" if use_hana else "SAP"
        reason = detail or (
            f"{label} PO lookup failed for {po_number} on connector {connector_id or 'mock'} ({err})."
        )
        return _result(
            po_number=po_number,
            po=None,
            reason=reason,
            connector_id=connector_id or None,
            lookup_error=err,
            backend=backend,
        )

    if po and isinstance(po, dict) and not po.get("lookup_error"):
        source = str(po.get("source") or backend)
        label = "HANA Cloud" if use_hana else "SAP"
        return _result(
            po_number=po_number,
            po=po,
            reason=f"{label} PO {po_number} found (source={source}).",
            connector_id=connector_id or None,
            backend=backend if source != "hana_cloud" else "hana",
        )

    connector_label = connector_id or "mock"
    label = "HANA" if use_hana else "SAP"
    return _result(
        po_number=po_number,
        po=None,
        reason=(
            f"{label} PO {po_number} not found on connector {connector_label}. "
            + (
                "Confirm PURCHASE_ORDERS in HANA includes this number."
                if use_hana
                else "Confirm samplePurchaseOrders / live SAP PO master includes this number."
            )
        ),
        connector_id=connector_id or None,
        lookup_error="not_found",
        backend=backend,
    )
