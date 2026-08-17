"""workflow_move_next — complete workflow step / Non-Invoice route."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillResult, invoice_from

SKILL_ID = "workflow_move_next"


async def run(ctx: ApContext) -> ApSkillResult:
    job = ctx.document_job or {}
    instance_id = str(job.get("instance_id") or "").strip()
    if not instance_id:
        return ApSkillResult(
            skill_id=SKILL_ID,
            credits=0,
            data={
                "skipped": True,
                "reason": "no instance_id",
                "ok": True,
            },
        )

    finalize = ctx.artifacts.get("finalize_decision")
    if not isinstance(finalize, dict) or not finalize:
        return ApSkillResult(
            skill_id=SKILL_ID,
            credits=0,
            data={
                "skipped": True,
                "reason": "no finalize_decision artifact",
                "instance_id": instance_id,
                "ok": True,
            },
        )

    decision = str(finalize.get("decision") or "")
    try:
        invoice = invoice_from(ctx)
    except Exception:
        invoice = {}
    doc_type = str(invoice.get("doc_type") or "invoice").lower()
    review = "Non-Invoice" if decision == "NON_INVOICE" or doc_type == "other" else decision

    payload = {
        "review": review,
        "decision": decision,
        "invoice_number": finalize.get("invoice_number"),
        "item_key": ctx.item_key,
        "run_id": ctx.run_id,
    }
    result = await ctx.ezofis.workflow_move_next(
        tenant_id=ctx.tenant_id,
        instance_id=instance_id,
        payload=payload,
    )
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "instance_id": instance_id,
            "review": review,
            "decision": decision,
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "response": result,
        },
    )
