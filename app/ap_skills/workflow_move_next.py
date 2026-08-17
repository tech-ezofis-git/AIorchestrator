"""workflow_move_next — complete workflow step / Non-Invoice route."""
from __future__ import annotations

from typing import Any, Optional

from app.ap_skills.types import ApContext, ApSkillResult, invoice_from

SKILL_ID = "workflow_move_next"


def _job_str(job: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        raw = job.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text.lower() != "none":
            return text
    return None


def _form_entry_id(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


async def run(ctx: ApContext) -> ApSkillResult:
    job = ctx.document_job or {}
    instance_id = _job_str(job, "instance_id")
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

    repository_id = _job_str(job, "repository_id")
    transaction_id = _job_str(job, "transaction_id")
    form_entry_id = _form_entry_id(_job_str(job, "form_entry_id"))
    process_id = _job_str(job, "process_id")
    workflow_id = _job_str(job, "workflow_id")
    form_id = _job_str(job, "form_id")
    item_id = _job_str(job, "repository_item_id", "item_id") or ctx.item_key

    payload = {
        "review": review,
        "decision": decision,
        "invoice_number": finalize.get("invoice_number"),
        "item_key": ctx.item_key,
        "run_id": ctx.run_id,
        "workflowId": workflow_id,
        "instanceId": instance_id,
        "transactionId": transaction_id,
        "processId": process_id,
        "repositoryId": repository_id,
        "itemId": item_id,
        "formId": form_id,
        "formEntryId": form_entry_id,
        "isItemTable": True,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
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
