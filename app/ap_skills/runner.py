"""AP skill registry and runner (Phase 1 + Phase 2)."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from app.ap_skills import (
    backorder_detect,
    duplicate_detect,
    extract_invoice,
    finalize_decision,
    gl_match,
    grn_match,
    matter_validate,
    po_lookup_quickbooks,
    po_lookup_sage,
    po_match,
    vendor_validate,
    workflow_move_next,
    workflow_progress,
)
from app.ap_skills.ap_metadata import extras_from_artifacts, merge_ids_into_job, push_extract_metadata, resolve_metadata_ids
from app.ap_skills.planner import maybe_reorder, resolve_skills
from app.ap_skills.store import ApStore
from app.ap_skills.types import (
    ApContext,
    ApSkillError,
    ApSkillResult,
)

logger = logging.getLogger("orchestrator.ap_runner")

SkillFn = Callable[[ApContext], Awaitable[ApSkillResult]]

REGISTRY: dict[str, SkillFn] = {
    extract_invoice.SKILL_ID: extract_invoice.run,
    po_lookup_quickbooks.SKILL_ID: po_lookup_quickbooks.run,
    po_lookup_sage.SKILL_ID: po_lookup_sage.run,
    po_match.SKILL_ID: po_match.run,
    gl_match.SKILL_ID: gl_match.run,
    grn_match.SKILL_ID: grn_match.run,
    duplicate_detect.SKILL_ID: duplicate_detect.run,
    vendor_validate.SKILL_ID: vendor_validate.run,
    matter_validate.SKILL_ID: matter_validate.run,
    backorder_detect.SKILL_ID: backorder_detect.run,
    finalize_decision.SKILL_ID: finalize_decision.run,
    workflow_progress.SKILL_ID: workflow_progress.run,
    workflow_move_next.SKILL_ID: workflow_move_next.run,
}


def resolve_item_key(job: dict[str, Any]) -> str:
    item_id = str(job.get("item_id") or job.get("repository_item_id") or "").strip()
    if item_id:
        return item_id
    filepath = str(job.get("filepath") or "").strip()
    if filepath:
        return filepath
    filename = str(job.get("filename") or "").strip()
    if filename:
        return f"upload:{filename}"
    invoice_json = job.get("invoice_json")
    if isinstance(invoice_json, dict) and invoice_json:
        canonical = json.dumps(invoice_json, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"invoice:{digest}"
    raise ApSkillError("AP document job needs filepath, file, invoice_json, or item_id.")


class ApSkillRunner:
    def __init__(
        self,
        *,
        store: ApStore,
        ezofis: Any,
        settings: Any,
        dispatcher: Any = None,
        llm: Any = None,
    ):
        self._store = store
        self._ezofis = ezofis
        self._settings = settings
        self._dispatcher = dispatcher
        self._llm = llm

    async def run(self, *, session_id: str, document_job: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(document_job.get("tenant_id") or "default").strip() or "default"
        item_key = resolve_item_key(document_job)
        plan = await self._store.get_plan(tenant_id)
        thresholds = dict((plan or {}).get("thresholds") or {})
        requested = document_job.get("skills")
        if requested is not None and not isinstance(requested, list):
            raise ApSkillError("payload.skills must be a list of skill ids.")

        # null skills → DEFAULT_SKILL_ORDER; list → exactly those ids.
        skills = resolve_skills(requested=requested)
        skills = await maybe_reorder(
            skills,
            llm=self._llm,
            use_planner=bool(getattr(self._settings, "ap_llm_planner", False)) and requested is None,
        )
        if not skills:
            raise ApSkillError("No skills to run.")

        run_id = await self._store.create_run(
            session_id=session_id,
            tenant_id=tenant_id,
            item_key=item_key,
            requested_skills=skills,
        )
        artifacts = await self._store.load_artifacts(tenant_id=tenant_id, item_key=item_key)
        invoice_json = document_job.get("invoice_json")
        extracted = artifacts.get("extract_invoice") or {}
        if not invoice_json and isinstance(extracted.get("invoice"), dict):
            invoice_json = extracted["invoice"]

        ctx = ApContext(
            tenant_id=tenant_id,
            item_key=item_key,
            run_id=run_id,
            session_id=session_id,
            invoice_json=invoice_json if isinstance(invoice_json, dict) else None,
            artifacts=artifacts,
            settings=self._settings,
            ezofis=self._ezofis,
            llm=self._llm,
            dispatcher=self._dispatcher,
            store=self._store,
            document_job=document_job,
            thresholds=thresholds,
            form_id=(str(document_job.get("form_id") or "").strip() or None),
        )
        try:
            ids = resolve_metadata_ids(document_job, ctx.form_id)
            looked = await self._store.fetch_ticket_context(
                tenant_id=tenant_id,
                instance_id=ids.get("instance_id") or None,
                repository_item_id=ids.get("item_id") or None,
                form_id=ids.get("form_id") or ctx.form_id,
            )
            if isinstance(looked, dict) and looked:
                merge_ids_into_job(document_job, looked)
                ids = resolve_metadata_ids(document_job, document_job.get("form_id") or ctx.form_id)
            if ids.get("form_id") and ids.get("form_entry_id") is None:
                latest = await self._store.latest_empty_ezfb_item(
                    tenant_id=tenant_id,
                    form_id=str(ids["form_id"]),
                )
                if latest is not None:
                    document_job["form_entry_id"] = str(latest)
            merge_ids_into_job(document_job, resolve_metadata_ids(document_job, document_job.get("form_id")))
            ctx.form_id = str(document_job.get("form_id") or "").strip() or ctx.form_id
            ctx.document_job = document_job
        except Exception as exc:
            logger.warning("ap_ticket_hydrate_failed", extra={"error_type": type(exc).__name__})
        form_controls = await self._store.fetch_form_controls(
            tenant_id=tenant_id,
            form_id=ctx.form_id,
        )

        skills_run: list[str] = []
        credits_charged = 0
        identify = item_key
        try:
            for skill_id in skills:
                handler = REGISTRY.get(skill_id)
                if handler is None:
                    raise ApSkillError(f"Unknown skill '{skill_id}'.")
                result = await handler(ctx)
                artifact = dict(result.data)
                ctx.artifacts[skill_id] = artifact
                if skill_id == "extract_invoice" and isinstance(artifact.get("invoice"), dict):
                    ctx.invoice_json = artifact["invoice"]
                    identify = (
                        artifact["invoice"].get("invoice_number")
                        or (artifact["invoice"].get("invoice_header") or {}).get("Invoice No")
                        or identify
                    )
                meta = await push_extract_metadata(
                    ezofis=self._ezofis,
                    tenant_id=tenant_id,
                    document_job=document_job,
                    form_id=ctx.form_id,
                    invoice=ctx.invoice_json or {},
                    extras=extras_from_artifacts(ctx.artifacts, skill_id),
                    skill_id=skill_id,
                    form_controls=form_controls,
                    store=self._store,
                )
                artifact["metadata_push"] = meta
                ctx.artifacts[skill_id] = artifact
                await self._store.save_artifact(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    item_key=item_key,
                    skill_id=skill_id,
                    result=artifact,
                )
                if result.credits > 0:
                    charge_status = await self._charge(
                        tenant_id=tenant_id,
                        skill_id=skill_id,
                        identify=str(identify),
                        credits=result.credits,
                    )
                    await self._store.record_credit(
                        run_id=run_id,
                        tenant_id=tenant_id,
                        skill_id=skill_id,
                        credits=result.credits,
                        identify=str(identify),
                        status=charge_status,
                    )
                    credits_charged += result.credits
                skills_run.append(skill_id)

            finalize = ctx.artifacts.get("finalize_decision") or {}
            decision = finalize.get("decision") or (ctx.artifacts.get("po_match") or {}).get("decision")
            await self._store.finish_run(
                run_id=run_id,
                tenant_id=tenant_id,
                status="completed",
                decision=decision,
                credits_charged=credits_charged,
            )
            return {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "item_key": item_key,
                "skills_run": skills_run,
                "credits_charged": credits_charged,
                "decision": decision,
                "artifacts": {k: ctx.artifacts[k] for k in skills_run},
            }
        except Exception:
            try:
                await self._store.finish_run(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    status="failed",
                    decision=None,
                    credits_charged=credits_charged,
                )
            except Exception:
                logger.warning("ap_run_fail_status_update_failed")
            raise

    async def _charge(self, *, tenant_id: str, skill_id: str, identify: str, credits: int) -> str:
        try:
            result = await self._ezofis.charge_activity_credit(
                tenant_id=tenant_id,
                skill_id=skill_id,
                identify=identify,
                credit=credits,
            )
            if isinstance(result, dict) and result.get("status") == "failed":
                return "failed"
            return "charged"
        except Exception:
            logger.warning("ap_credit_charge_failed", extra={"skill_id": skill_id})
            return "failed"


__all__ = ["ApSkillRunner", "REGISTRY", "resolve_item_key"]
