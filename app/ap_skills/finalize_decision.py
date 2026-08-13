"""finalize_decision — combine skill artifacts into a single AP decision."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from

SKILL_ID = "finalize_decision"

_RANK = {
    "MATCHED": 3,
    "PARTIALLY_MATCHED": 2,
    "NOT_MATCHED": 1,
    "DUPLICATE": 0,
    "NON_INVOICE": 0,
}


def _worse(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return left if _RANK.get(left, 0) <= _RANK.get(right, 0) else right


async def run(ctx: ApContext) -> ApSkillResult:
    try:
        invoice = invoice_from(ctx)
    except Exception:
        invoice = {}

    doc_type = str(invoice.get("doc_type") or "invoice").lower()
    duplicate = ctx.artifacts.get("duplicate_detect") or {}
    po_match = ctx.artifacts.get("po_match") or {}
    gl_match = ctx.artifacts.get("gl_match") or {}
    grn_match = ctx.artifacts.get("grn_match") or {}
    vendor = ctx.artifacts.get("vendor_validate") or {}
    matter = ctx.artifacts.get("matter_validate") or {}
    backorder = ctx.artifacts.get("backorder_detect") or {}

    if doc_type == "other":
        decision = "NON_INVOICE"
        reason = "Document was not classified as an AP invoice."
    elif duplicate.get("is_duplicate_invoice"):
        decision = "DUPLICATE"
        reason = f"Duplicate of {duplicate.get('duplicate_of')}."
    else:
        decision = ""
        reason = ""
        po_decision = str(po_match.get("decision") or "")
        gl_decision = str(gl_match.get("decision") or "")
        grn_decision = str(grn_match.get("decision") or "")
        vendor_status = str(vendor.get("status") or "")
        matter_status = str(matter.get("status") or "")

        for candidate, why in (
            (po_decision, po_match.get("reason")),
            (gl_decision, gl_match.get("reason")),
            (grn_decision, grn_match.get("reason")),
        ):
            if candidate:
                decision = _worse(decision, candidate)
                if why and (not reason or decision == candidate):
                    reason = str(why)

        if vendor_status == "MISMATCH":
            decision = _worse(decision, "PARTIALLY_MATCHED" if decision == "MATCHED" else "NOT_MATCHED")
            reason = vendor.get("reason") or reason or "Vendor validation failed."
        if matter_status == "NOT_IN_MASTER":
            decision = _worse(decision, "PARTIALLY_MATCHED")
            reason = matter.get("reason") or reason or "Matter ID not in master."

        if not decision:
            if vendor_status in ("ACTIVE", "MISSING"):
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
            "matter_status": matter.get("status") if isinstance(matter, dict) else None,
            "gl_decision": gl_match.get("decision") if isinstance(gl_match, dict) else None,
            "grn_decision": grn_match.get("decision") if isinstance(grn_match, dict) else None,
        },
    )
