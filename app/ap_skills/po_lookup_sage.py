"""po_lookup_sage — PO from Sage when no PO master form is configured."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_text, invoice_from

SKILL_ID = "po_lookup_sage"


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    if not po_number:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": None,
                "po": None,
                "source": "sage",
                "reason": "Invoice has no PO number.",
            },
        )

    job = ctx.document_job or {}
    resource = str(job.get("resource") or ctx.thresholds.get("po_resource") or "").strip().upper()
    if resource and resource not in ("SAGE",):
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": po_number,
                "po": None,
                "source": "sage",
                "skipped": True,
                "reason": f"Resource is {resource}, not SAGE.",
            },
        )

    # Prefer existing QuickBooks PO if already looked up.
    qb = ctx.artifacts.get("po_lookup_quickbooks") or {}
    if isinstance(qb, dict) and isinstance(qb.get("po"), dict):
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": po_number,
                "po": None,
                "source": "sage",
                "skipped": True,
                "reason": "QuickBooks PO already available; Sage lookup skipped.",
            },
        )

    connector_id = str(job.get("connector_id") or ctx.thresholds.get("sage_connector_id") or "").strip()
    if not connector_id and hasattr(ctx.ezofis, "_live_enabled") and ctx.ezofis._live_enabled():
        raise ApSkillError("po_lookup_sage requires payload.connector_id when live Ezofis is enabled.")

    po = await ctx.ezofis.lookup_po_sage(
        tenant_id=ctx.tenant_id,
        po_number=po_number,
        connector_id=connector_id or "mock",
    )
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "po_number": po_number,
            "po": po,
            "source": "sage",
            "connector_id": connector_id or None,
            "reason": f"Sage PO {po_number} found." if po else f"Sage PO {po_number} not found.",
        },
    )
