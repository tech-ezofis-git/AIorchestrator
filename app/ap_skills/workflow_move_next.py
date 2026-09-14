"""workflow_move_next — complete workflow step / Non-Invoice route."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Union

from app.ap_skills.hana_po import is_hana_po_connector, resolve_hana_connector_id
from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from

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


def form_entry_id_for_v6_move_next(form_entry_id: Any) -> Optional[Union[int, str]]:
    """V6 MoveToNextStepRequest.FormEntryId is Guid?. Send dashed GUID or a legacy int."""
    if form_entry_id is None or isinstance(form_entry_id, bool):
        return None
    if isinstance(form_entry_id, int):
        return form_entry_id if form_entry_id > 0 else None
    text = str(form_entry_id).strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


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

    from app.ap_skills.agent_validation_response import build_aiagent_response

    agent_response = build_aiagent_response(
        decision=decision,
        reason=str(finalize.get("reason") or "").strip(),
        artifacts={**ctx.artifacts, "finalize_decision": finalize},
        invoice=invoice,
        document_job=job,
        ai_insight=str(finalize.get("ai_insight") or "").strip(),
        source_type=str(finalize.get("source_type") or "").strip(),
    )
    # Non-Invoice / empty reason fallback for workflow comments.
    comments = str(agent_response.get("reason") or "").strip() or (
        f"Classified as {doc_type}" if review == "Non-Invoice" else review
    )
    # Keep move-next review label in sync with response decision.
    if agent_response.get("decision"):
        review = str(agent_response["decision"])

    repository_id = _job_str(job, "repository_id")
    transaction_id = _job_str(job, "transaction_id")
    form_entry_id = _form_entry_id(_job_str(job, "form_entry_id", "formEntryId", "formentryId"))
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
        "AIAGENTResponse": agent_response,
        "itemId": item_id,
        "repositoryId": repository_id,
        "formId": form_id,
        "isItemTable": True,
    }
    po_match = ctx.artifacts.get("po_match") or {}
    if form_entry_id is not None:
        entry = form_entry_id_for_v6_move_next(form_entry_id)
        if entry is not None:
            payload["formEntryId"] = entry
    payload = {k: v for k, v in payload.items() if v is not None}

    hana_match: dict[str, Any] | None = None
    connector_id = resolve_hana_connector_id(
        tenant_id=ctx.tenant_id,
        connector_id=str(job.get("connector_id") or "").strip(),
    )
    po_lookup = ctx.artifacts.get("po_lookup_sap") or {}
    use_hana = is_hana_po_connector(connector_id) or po_lookup.get("source") == "hana"
    if use_hana and decision.upper() in {"MATCHED", "PARTIALLY_MATCHED"}:
        po_number = str(finalize.get("po_number") or po_match.get("po_number") or "").strip()
        invoice_number = str(
            finalize.get("invoice_number")
            or field_text(invoice, "invoice_number", "invoiceNumber", "invoice_no")
            or ""
        ).strip()
        if po_number and invoice_number and connector_id:
            match_status = "Matched" if decision.upper() == "MATCHED" else "Partially Matched"
            try:
                hana_match = await ctx.ezofis.save_hana_po_invoice_match(
                    tenant_id=ctx.tenant_id,
                    connector_id=connector_id,
                    po_number=po_number,
                    instance_id=instance_id,
                    invoice_number=invoice_number,
                    status=match_status,
                )
            except Exception as exc:
                logger.warning(
                    "hana_po_match_save_failed",
                    extra={"instance_id": instance_id, "po_number": po_number},
                )
                hana_match = {"ok": False, "error": type(exc).__name__}

    result = await ctx.ezofis.workflow_move_next(
        tenant_id=ctx.tenant_id,
        instance_id=instance_id,
        payload=payload,
    )
    data: dict[str, Any] = {
        "instance_id": instance_id,
        "activityid": activity_id,
        "review": review,
        "decision": decision,
        "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
        "response": result,
    }
    if hana_match is not None:
        data["hana_po_match"] = hana_match
    return ApSkillResult(
        skill_id=SKILL_ID,
        data=data,
    )
