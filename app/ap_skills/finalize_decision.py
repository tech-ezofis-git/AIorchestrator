"""finalize_decision — combine skill artifacts into a single AP decision."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from

SKILL_ID = "finalize_decision"


async def run(ctx: ApContext) -> ApSkillResult:
    try:
        invoice = invoice_from(ctx)
    except Exception:
        invoice = {}

    doc_type = str(invoice.get("doc_type") or "invoice").lower()
    duplicate = ctx.artifacts.get("duplicate_detect") or {}
    po_match = ctx.artifacts.get("po_match") or {}
    vendor = ctx.artifacts.get("vendor_validate") or {}
    backorder = ctx.artifacts.get("backorder_detect") or {}

    if doc_type == "other":
        decision = "NON_INVOICE"
        reason = "Document was not classified as an AP invoice."
    elif duplicate.get("is_duplicate_invoice"):
        decision = "DUPLICATE"
        reason = f"Duplicate of {duplicate.get('duplicate_of')}."
    else:
        po_decision = str(po_match.get("decision") or "")
        vendor_status = str(vendor.get("status") or "")
        if vendor_status == "MISMATCH" and po_decision == "MATCHED":
            decision = "PARTIALLY_MATCHED"
            reason = vendor.get("reason") or "Vendor mismatch on an otherwise matching PO."
        elif po_decision:
            decision = po_decision
            reason = po_match.get("reason") or f"PO match decision {po_decision}."
        elif vendor_status == "MISMATCH":
            decision = "NOT_MATCHED"
            reason = vendor.get("reason") or "Vendor validation failed."
        elif vendor_status in ("ACTIVE", "MISSING"):
            decision = "PARTIALLY_MATCHED" if vendor_status == "MISSING" else "MATCHED"
            reason = vendor.get("reason") or "Vendor validation only."
        else:
            decision = "PARTIALLY_MATCHED"
            reason = "Not enough matching evidence to auto-approve."

    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "decision": decision,
            "reason": reason,
            "invoice_number": field_text(invoice, "invoice_number") or None,
            "po_number": (po_match.get("po_number") if isinstance(po_match, dict) else None),
            "duplicate": bool(duplicate.get("is_duplicate_invoice")),
            "backorder": bool(backorder.get("detected")),
        },
    )
