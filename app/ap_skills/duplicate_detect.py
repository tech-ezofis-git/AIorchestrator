"""duplicate_detect — same invoice number vs prior artifacts / cloud history."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from, name_similarity

SKILL_ID = "duplicate_detect"


def _invoice_of(payload: dict) -> dict:
    inner = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else payload
    return inner if isinstance(inner, dict) else {}


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    invoice_number = field_text(invoice, "invoice_number")
    vendor = field_text(invoice, "vendor", "supplier")
    history = []
    if ctx.store is not None:
        history.extend(
            await ctx.store.list_skill_artifacts(tenant_id=ctx.tenant_id, skill_id="extract_invoice")
        )
    cloud_history = await ctx.ezofis.lookup_invoice_history(
        tenant_id=ctx.tenant_id, invoice_number=invoice_number or None
    )
    if isinstance(cloud_history, list):
        history.extend(cloud_history)

    duplicate_of = None
    score = 0.0
    for prior in history:
        if not isinstance(prior, dict):
            continue
        if prior.get("item_key") and prior.get("item_key") == ctx.item_key:
            continue
        prior_inv = _invoice_of(prior)
        prior_no = field_text(prior_inv, "invoice_number")
        if invoice_number and prior_no and invoice_number.lower() == prior_no.lower():
            duplicate_of = prior_no
            score = 1.0
            break
        prior_vendor = field_text(prior_inv, "vendor", "supplier")
        if invoice_number and prior_no and name_similarity(vendor, prior_vendor) >= 0.85:
            continue

    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "is_duplicate_invoice": bool(duplicate_of),
            "duplicate_of": duplicate_of,
            "duplicate_score": score,
            "checked_history": len(history),
        },
    )
