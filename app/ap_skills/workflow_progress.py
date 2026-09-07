"""workflow_progress — kept for payload.skills back-compat.

Live progress is owned by ApSkillRunner (instance PATCH). When the runner
already reported, this skill is a no-op so Hangfire payloads that still
list it do not double-PATCH or charge.
"""
from __future__ import annotations

from app.ap_skills.ap_progress import RUNNER_OWNED_FLAG
from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult

SKILL_ID = "workflow_progress"


async def run(ctx: ApContext) -> ApSkillResult:
    job = ctx.document_job or {}
    if job.get(RUNNER_OWNED_FLAG):
        return ApSkillResult(
            skill_id=SKILL_ID,
            credits=0,
            data={"skipped": True, "reason": "runner_owned"},
        )
    workflow_id = str(job.get("workflow_id") or "").strip()
    instance_id = str(job.get("instance_id") or "").strip()
    if not workflow_id or not instance_id:
        raise ApSkillError(
            "workflow_progress requires payload.workflow_id and payload.instance_id."
        )

    finalize = ctx.artifacts.get("finalize_decision") or {}
    decision = str(finalize.get("decision") or (ctx.artifacts.get("po_match") or {}).get("decision") or "")
    stage = "FINALIZE" if decision else "RUNNING"
    message = finalize.get("reason") or f"AP skills completed for {ctx.item_key}."
    percent = 100 if decision else 80

    result = await ctx.ezofis.report_ap_progress(
        tenant_id=ctx.tenant_id,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage=stage,
        message=str(message),
        percent=percent,
    )
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "workflow_id": workflow_id,
            "instance_id": instance_id,
            "stage": stage,
            "message": message,
            "percent": percent,
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "response": result,
        },
    )
