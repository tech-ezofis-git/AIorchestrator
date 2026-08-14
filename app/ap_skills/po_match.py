"""po_match — invoice ↔ purchase order (masters via Ezofis client)."""
from __future__ import annotations

from typing import Any

from app.ap_skills.types import (
    ApContext,
    ApSkillResult,
    decision_from_score,
    field_number,
    field_text,
    invoice_from,
    name_similarity,
)

SKILL_ID = "po_match"


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    if not po_number:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": None,
                "po": None,
                "score": 0,
                "decision": "NOT_MATCHED",
                "reason": "Invoice has no PO number.",
            },
        )

    po = None
    for connector_skill in ("po_lookup_quickbooks", "po_lookup_sage"):
        artifact = ctx.artifacts.get(connector_skill) or {}
        if isinstance(artifact, dict) and isinstance(artifact.get("po"), dict):
            po = artifact["po"]
            break
    if not po:
        form_id = (ctx.form_id or str(ctx.document_job.get("form_id") or "").strip() or None)
        po = await ctx.ezofis.lookup_po(
            tenant_id=ctx.tenant_id,
            po_number=po_number,
            form_id=form_id,
        )
    if not po:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": po_number,
                "po": None,
                "score": 0,
                "decision": "NOT_MATCHED",
                "reason": f"PO {po_number} was not found.",
            },
        )

    approved = int(ctx.thresholds.get("approved") or ctx.settings.ap_approved_threshold)
    partial = int(ctx.thresholds.get("partial") or ctx.settings.ap_partial_threshold)
    tolerance = float(ctx.thresholds.get("amount_tolerance") or ctx.settings.ap_amount_tolerance)

    score = 40.0
    reasons: list[str] = [f"PO {po_number} found."]
    inv_vendor = field_text(invoice, "vendor", "supplier")
    po_vendor = field_text(po, "vendor", "supplier", "Vendor Name", "Vendor")
    sim = name_similarity(inv_vendor, po_vendor) if inv_vendor and po_vendor else 0.0
    score += round(30.0 * sim, 2)
    if sim >= 0.85:
        reasons.append("Vendor matches PO.")
    elif inv_vendor and po_vendor:
        reasons.append("Vendor differs from PO vendor.")

    inv_total = field_number(invoice, "total", "amount")
    po_total = field_number(po, "total", "amount")
    if inv_total is not None and po_total is not None and po_total != 0:
        delta = abs(inv_total - po_total) / abs(po_total)
        if delta <= tolerance:
            score += 30.0
            reasons.append("Totals match within tolerance.")
        else:
            reasons.append("Totals differ.")
    elif inv_total is None or po_total is None:
        reasons.append("Missing total on invoice or PO.")

    score = min(100.0, round(score, 2))
    data: dict[str, Any] = {
        "po_number": po_number,
        "po": po,
        "score": score,
        "decision": decision_from_score(score, approved=approved, partial=partial),
        "vendor_similarity": sim,
        "reason": " ".join(reasons),
    }
    if isinstance(po, dict) and po.get("form_id"):
        data["form_id"] = po.get("form_id")
    if isinstance(po, dict) and po.get("ezfb_table"):
        data["ezfb_table"] = po.get("ezfb_table")
    return ApSkillResult(
        skill_id=SKILL_ID,
        data=data,
    )
