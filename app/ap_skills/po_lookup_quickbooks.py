"""po_lookup_quickbooks — PO from QuickBooks connector when resource=QUICKBOOKS."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_text, invoice_from

SKILL_ID = "po_lookup_quickbooks"


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    if not po_number:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": None,
                "po": None,
                "source": "quickbooks",
                "reason": "Invoice has no PO number.",
            },
        )

    job = ctx.document_job or {}
    connector_id = str(job.get("connector_id") or ctx.thresholds.get("quickbooks_connector_id") or "").strip()
    resource = str(job.get("resource") or ctx.thresholds.get("po_resource") or "").strip().upper()
    if resource and resource not in ("QUICKBOOKS", "QB"):
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": po_number,
                "po": None,
                "source": "quickbooks",
                "skipped": True,
                "reason": f"Resource is {resource}, not QUICKBOOKS.",
            },
        )
    if not connector_id and ctx.ezofis._live_enabled() if hasattr(ctx.ezofis, "_live_enabled") else False:
        raise ApSkillError("po_lookup_quickbooks requires payload.connector_id when live Ezofis is enabled.")

    po = await ctx.ezofis.lookup_po_quickbooks(
        tenant_id=ctx.tenant_id,
        po_number=po_number,
        connector_id=connector_id or "mock",
    )
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "po_number": po_number,
            "po": po,
            "source": "quickbooks",
            "connector_id": connector_id or None,
            "reason": (
                f"QuickBooks PO {po_number} found."
                if po
                else f"QuickBooks PO {po_number} not found."
            ),
        },
    )
