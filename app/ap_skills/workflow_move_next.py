"""workflow_move_next — complete workflow step / Non-Invoice route."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.ap_skills.types import ApContext, ApSkillResult, invoice_from

logger = logging.getLogger("orchestrator.ap.workflow_move_next")

SKILL_ID = "workflow_move_next"

# Workflow UI / .NET move-next review strings (apagentv6 utils.py).
_REVIEW_LABELS = {
    "MATCHED": "Matched",
    "PARTIALLY_MATCHED": "Partially Matched",
    "NOT_MATCHED": "Not Matched",
    "NON_INVOICE": "Non-Invoice",
    "DUPLICATE": "Not Matched",
}


def _review_label(decision: str, *, doc_type: str = "") -> str:
    if decision == "NON_INVOICE" or str(doc_type or "").lower() == "other":
        return "Non-Invoice"
    mapped = _REVIEW_LABELS.get(str(decision or "").strip().upper())
    if mapped:
        return mapped
    raw = str(decision or "").strip()
    if raw in ("Matched", "Partially Matched", "Not Matched", "Non-Invoice"):
        return raw
    return "Not Matched"


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
    text = str(raw).strip()
    return text or None


async def _resolve_activity_id(ctx: ApContext, job: dict[str, Any], workflow_id: Optional[str]) -> Optional[str]:
    explicit = _job_str(job, "activity_id")
    if explicit:
        return explicit
    store = ctx.store
    if store is None or not hasattr(store, "fetch_workflow_activity_id"):
        return None
    step_name = str(getattr(ctx.settings, "ap_agent_workflow_step_name", None) or "AP AGENT 1").strip()
    return await store.fetch_workflow_activity_id(
        tenant_id=ctx.tenant_id,
        workflow_id=workflow_id,
        step_name=step_name or "AP AGENT 1",
    )


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

    if finalize.get("used_mock_data"):
        # Code-review findings #3/#4: never advance the real workflow off
        # a decision that finalize_decision itself flagged as reached
        # using a mocked PO/vendor master record (EZOFIS live login not
        # configured, or the live lookup came back empty) — same
        # not-reliable-enough-to-post treatment as a missing instance_id.
        logger.warning(
            "ap_move_next_skipped_mock_data",
            extra={"instance_id": instance_id},
        )
        return ApSkillResult(
            skill_id=SKILL_ID,
            credits=0,
            data={
                "skipped": True,
                "reason": "used_mock_data",
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
    review = _review_label(decision, doc_type=doc_type)
    comments = str(finalize.get("reason") or "").strip() or (
        f"Classified as {doc_type}" if review == "Non-Invoice" else review
    )

    repository_id = _job_str(job, "repository_id")
    transaction_id = _job_str(job, "transaction_id")
    form_entry_id = _form_entry_id(_job_str(job, "form_entry_id"))
    process_id = _job_str(job, "process_id")
    workflow_id = _job_str(job, "workflow_id")
    form_id = _job_str(job, "form_id")
    item_id = _job_str(job, "repository_item_id", "item_id") or ctx.item_key
    activity_id = await _resolve_activity_id(ctx, job, workflow_id)
    if not activity_id:
        return ApSkillResult(
            skill_id=SKILL_ID,
            credits=0,
            data={
                "skipped": True,
                "reason": "no activityid",
                "instance_id": instance_id,
                "ok": True,
            },
        )

    payload = {
        "activityid": activity_id,
        "review": review,
        "comments": comments,
        "workflowId": workflow_id,
        "transactionId": transaction_id,
        "instanceId": instance_id,
        "processId": process_id,
        "AIAGENTResponse": {
            "decision": review,
            "reason": finalize.get("reason"),
            "invoice_number": finalize.get("invoice_number"),
            "po_number": finalize.get("po_number"),
            "duplicate": finalize.get("duplicate"),
            "backorder": finalize.get("backorder"),
            "run_id": ctx.run_id,
            "item_key": ctx.item_key,
        },
        "itemId": item_id,
        "repositoryId": repository_id,
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
            "activityid": activity_id,
            "review": review,
            "decision": decision,
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "response": result,
        },
    )
