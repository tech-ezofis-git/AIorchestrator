"""grn_match — invoice ↔ goods receipt note (masters via Ezofis)."""
from __future__ import annotations

from app.ap_skills.types import (
    ApContext,
    ApSkillResult,
    decision_from_score,
    field_number,
    field_text,
    invoice_from,
    match_lines_by_description,
    name_similarity,
)

SKILL_ID = "grn_match"


def _description(line: dict) -> str:
    return field_text(line, "description", "item", "name", "Description")


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    grn_number = field_text(invoice, "grn_number", "grn", "GRN Number", "receipt_number")
    grn = await ctx.ezofis.lookup_grn(
        tenant_id=ctx.tenant_id,
        grn_number=grn_number or None,
        po_number=po_number or None,
    )
    if not grn:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "grn_number": grn_number or None,
                "po_number": po_number or None,
                "grn": None,
                "score": 0,
                "decision": "NOT_MATCHED",
                "reason": "No GRN found for this invoice.",
            },
        )

    approved = int(ctx.thresholds.get("approved") or ctx.settings.ap_approved_threshold)
    partial = int(ctx.thresholds.get("partial") or ctx.settings.ap_partial_threshold)
    score = 40.0
    reasons = [f"GRN {field_text(grn, 'grn_number', 'number') or grn_number or 'found'}."]

    inv_vendor = field_text(invoice, "vendor", "supplier")
    grn_vendor = field_text(grn, "vendor", "supplier", "Vendor Name")
    sim = name_similarity(inv_vendor, grn_vendor) if inv_vendor and grn_vendor else 0.0
    score += round(25.0 * sim, 2)

    raw_inv_lines = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    inv_lines = [line for line in raw_inv_lines if isinstance(line, dict)]
    raw_grn_lines = grn.get("lines") if isinstance(grn.get("lines"), list) else []
    grn_lines = [line for line in raw_grn_lines if isinstance(line, dict)]
    # Code-review finding (ultrareview altitude fix): this used to pair
    # inv_lines[index] with grn_lines[index] purely by array position —
    # the same bug backorder_detect.py had (finding #12) before being
    # fixed to match by description instead, since GRN lines don't have
    # to be listed in the same order as the invoice's lines.
    matched_indexes = match_lines_by_description(inv_lines, grn_lines, describe=_description)
    line_hits = 0
    for inv_line, grn_index in zip(inv_lines, matched_indexes):
        inv_qty = field_number(inv_line, "qty", "quantity") or 0.0
        if grn_index is not None:
            grn_qty = field_number(grn_lines[grn_index], "qty", "quantity", "received_qty") or 0.0
            if inv_qty and abs(inv_qty - grn_qty) < 0.001:
                line_hits += 1
            elif not inv_qty and grn_qty:
                line_hits += 1
    if inv_lines:
        score += round(35.0 * (line_hits / max(len(inv_lines), 1)), 2)
        reasons.append(f"{line_hits}/{len(inv_lines)} line qty matched GRN.")
    else:
        inv_total = field_number(invoice, "total", "amount")
        grn_total = field_number(grn, "total", "amount")
        if inv_total is not None and grn_total is not None and abs(inv_total - grn_total) < 0.01:
            score += 35.0
            reasons.append("Invoice total matches GRN total.")

    score = min(100.0, round(score, 2))
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "grn_number": field_text(grn, "grn_number", "number") or grn_number or None,
            "po_number": po_number or None,
            "grn": grn,
            "score": score,
            "decision": decision_from_score(score, approved=approved, partial=partial),
            "vendor_similarity": sim,
            "reason": " ".join(reasons),
        },
    )
