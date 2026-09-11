"""po_lookup_sap — PO from SAP connector when resource=SAP (ECC/S4/XSUAA)."""
from __future__ import annotations

from typing import Any

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
    # SAP ECC, SAP S/4 HANA, etc.
    return compact.startswith("SAP") or slashless.startswith("SAP")


def _result(
    *,
    po_number: str | None,
    po: dict[str, Any] | None,
    reason: str,
    connector_id: str | None = None,
    skipped: bool = False,
    lookup_error: str | None = None,
) -> ApSkillResult:
    data: dict[str, Any] = {
        "po_number": po_number,
        "po": po,
        "source": "sap",
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
    connector_id = str(
        job.get("connector_id")
        or ctx.thresholds.get("sap_connector_id")
        or ""
    ).strip()
    resource = str(job.get("resource") or ctx.thresholds.get("po_resource") or "").strip().upper()
    if resource and not _is_sap_resource(resource):
        return _result(
            po_number=po_number,
            po=None,
            reason=f"Resource is {resource}, not SAP — SAP purchase order lookup skipped.",
            skipped=True,
        )

    live = hasattr(ctx.ezofis, "_live_enabled") and ctx.ezofis._live_enabled()
    if not connector_id and live:
        raise ApSkillError(
            "po_lookup_sap requires payload.connector_id (SAP connector GUID) when live Ezofis is enabled."
        )

    po = await ctx.ezofis.lookup_po_sap(
        tenant_id=ctx.tenant_id,
        po_number=po_number,
        connector_id=connector_id or "mock",
    )

    if isinstance(po, dict) and po.get("lookup_error"):
        err = str(po.get("lookup_error") or "request_failed")
        detail = str(po.get("reason") or "").strip()
        reason = detail or (
            f"SAP PO lookup failed for {po_number} on connector {connector_id or 'mock'} ({err})."
        )
        return _result(
            po_number=po_number,
            po=None,
            reason=reason,
            connector_id=connector_id or None,
            lookup_error=err,
        )

    if po:
        source = str(po.get("source") or "sap")
        return _result(
            po_number=po_number,
            po=po,
            reason=f"SAP PO {po_number} found (source={source}).",
            connector_id=connector_id or None,
        )

    connector_label = connector_id or "mock"
    return _result(
        po_number=po_number,
        po=None,
        reason=(
            f"SAP PO {po_number} not found on connector {connector_label}. "
            "Confirm samplePurchaseOrders / live SAP PO master includes this number."
        ),
        connector_id=connector_id or None,
        lookup_error="not_found",
    )
