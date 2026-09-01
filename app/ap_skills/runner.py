"""AP skill registry and runner (Phase 1 + Phase 2)."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
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

# ApRunInProgressError (raised by ApStore.create_run on a concurrent
# duplicate) is intentionally not imported/caught here — it propagates
# straight through ApSkillRunner.run to the caller (app/main.py maps it to
# HTTP 409), same as ApStoreUnavailableError does.

logger = logging.getLogger("orchestrator.ap_runner")

SkillFn = Callable[[ApContext], Awaitable[ApSkillResult]]

# Statuses a prior run can be in for it to be eligible for the
# dedupe-window short-circuit below — a "running" run is handled entirely
# by the DB-level unique-index conflict in ApStore.create_run instead (a
# genuinely concurrent duplicate, rejected outright, no window involved).
_DEDUPE_ELIGIBLE_STATUSES = frozenset({"completed", "completed_low_confidence"})


def _run_status_and_quality(
    artifacts: dict[str, Any], finalize: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Decide ap_runs.status + the data_quality summary persisted with it.

    Code-review finding #3: a run used to report "completed" regardless of
    whether the extraction/decision was actually usable. Now reports
    "completed_low_confidence" instead when either: the extraction found
    none of {invoice_number, vendor, po_number, total} (extract_invoice's
    own `data_quality`, see app/ap_skills/extract_invoice.py's
    `_completeness`), or finalize_decision flagged that a mocked PO/vendor
    master record was used to reach its decision (finding #4,
    `finalize_decision`'s `used_mock_data`). `workflow_move_next` reads
    `finalize_decision.used_mock_data` directly to decide whether to skip
    posting to the real workflow off unreliable data."""
    extract = artifacts.get("extract_invoice") or {}
    extract_quality = extract.get("data_quality") or {}
    ocr_mock = bool(extract.get("ocr_mock"))
    used_mock_data = bool(finalize.get("used_mock_data"))
    low_confidence = bool(extract_quality) and extract_quality.get("fields_found", 0) == 0
    status = "completed_low_confidence" if (low_confidence or used_mock_data) else "completed"
    data_quality = {
        "extract": extract_quality or None,
        "ocr_mock": ocr_mock,
        "used_mock_data": used_mock_data,
        "low_confidence": low_confidence,
    }
    return status, data_quality


def _within_dedupe_window(finished_at: Any, window_seconds: float) -> bool:
    if finished_at is None or window_seconds <= 0:
        return False
    if not isinstance(finished_at, datetime):
        return False
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - finished_at).total_seconds()
    return 0 <= age <= window_seconds

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

        # Code-review finding #2: a retried/duplicate submission of the
        # DEFAULT pipeline (payload.skills omitted) for the same item
        # shortly after a prior run already completed re-runs every skill,
        # re-pushes metadata, and re-charges credits. Short-circuit to
        # that prior run's stored result instead, unless the caller
        # explicitly asks to force a fresh run (payload.force_rerun) — e.g.
        # a legitimate re-extraction after fixing bad source data. Never
        # applies when `skills` was explicitly requested: that's AP's
        # documented "re-run one skill from stored artifacts" feature
        # (a deliberately different operation, not a duplicate submission)
        # and must always actually run. A genuinely concurrent duplicate
        # (still "running") is a separate case, handled below by
        # create_run's unique-index conflict, not by this window.
        #
        # (ultrareview fix: this now runs BEFORE resolve_skills/
        # maybe_reorder — it used to run after, so a duplicate submission
        # still paid for the optional LLM planner reorder call
        # (AP_LLM_PLANNER) every time before being short-circuited, only
        # to discard the reordered list on the dedupe path.)
        if requested is None and not document_job.get("force_rerun"):
            latest_run = await self._store.get_latest_run(tenant_id=tenant_id, item_key=item_key)
            if (
                latest_run
                and latest_run.get("status") in _DEDUPE_ELIGIBLE_STATUSES
                and _within_dedupe_window(
                    latest_run.get("finished_at"),
                    float(getattr(self._settings, "ap_dedupe_window_seconds", 300) or 300),
                )
            ):
                artifacts = await self._store.load_artifacts(tenant_id=tenant_id, item_key=item_key)
                logger.info(
                    "ap_run_deduplicated",
                    extra={
                        "tenant_id": tenant_id,
                        "item_key": item_key,
                        "source_run_id": latest_run["id"],
                    },
                )
                return {
                    "run_id": latest_run["id"],
                    "tenant_id": tenant_id,
                    "item_key": item_key,
                    "skills_run": list(artifacts.keys()),
                    "credits_charged": latest_run.get("credits_charged") or 0,
                    "decision": latest_run.get("decision"),
                    "status": latest_run.get("status"),
                    "data_quality": latest_run.get("data_quality"),
                    # No new LLM call was made — this run's own token spend
                    # is 0 by definition, not a lost/uncaptured figure.
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "artifacts": artifacts,
                    "deduplicated": True,
                }

        # null skills → DEFAULT_SKILL_ORDER; list → exactly those ids.
        skills = resolve_skills(requested=requested)
        skills = await maybe_reorder(
            skills,
            llm=self._llm,
            use_planner=bool(getattr(self._settings, "ap_llm_planner", False)) and requested is None,
            llm_overrides=document_job.get("llm_overrides"),
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
            llm_overrides=document_job.get("llm_overrides"),
            llm_fallback_overrides=document_job.get("llm_fallback_overrides"),
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
                if latest:
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
        # Code-review finding #5: sums every skill's own real LLM usage
        # (today, only extract_invoice makes an LLM call) into the run's
        # total instead of the previous hardcoded 0 — see ApAgent.handle
        # and EzofisClient.charge_activity_credit.
        token_usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            for skill_id in skills:
                handler = REGISTRY.get(skill_id)
                if handler is None:
                    raise ApSkillError(f"Unknown skill '{skill_id}'.")
                result = await handler(ctx)
                artifact = dict(result.data)
                ctx.artifacts[skill_id] = artifact
                skill_usage = artifact.get("usage") if isinstance(artifact.get("usage"), dict) else None
                if skill_usage:
                    for key in token_usage_total:
                        token_usage_total[key] += int(skill_usage.get(key) or 0)
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
                try:
                    await self._store.save_artifact(
                        run_id=run_id,
                        tenant_id=tenant_id,
                        item_key=item_key,
                        skill_id=skill_id,
                        result=artifact,
                    )
                except Exception as exc:
                    # Metadata was already pushed. Failing the run here would
                    # retry the skill and PATCH V6 a second time.
                    logger.error(
                        "ap_artifact_persist_orphaned",
                        extra={
                            "run_id": run_id,
                            "tenant_id": tenant_id,
                            "skill_id": skill_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                if result.credits > 0:
                    charge_status = await self._charge(
                        tenant_id=tenant_id,
                        skill_id=skill_id,
                        identify=str(identify),
                        credits=result.credits,
                        usage=skill_usage,
                    )
                    try:
                        await self._store.record_credit(
                            run_id=run_id,
                            tenant_id=tenant_id,
                            skill_id=skill_id,
                            credits=result.credits,
                            identify=str(identify),
                            status=charge_status,
                        )
                    except Exception as exc:
                        # Code-review finding #9: the external credit
                        # charge above already happened (or was
                        # attempted) — if the LOCAL ledger write then
                        # fails, the worse outcome is letting this
                        # exception abort the whole run (marks it
                        # "failed", and a caller retry would charge this
                        # skill's credit AGAIN). Log loudly for manual
                        # reconciliation instead and keep going; a missing
                        # ledger row is recoverable, a double charge isn't.
                        logger.error(
                            "ap_credit_orphaned",
                            extra={
                                "run_id": run_id,
                                "tenant_id": tenant_id,
                                "skill_id": skill_id,
                                "credits": result.credits,
                                "charge_status": charge_status,
                                "error_type": type(exc).__name__,
                            },
                        )
                    credits_charged += result.credits
                skills_run.append(skill_id)

            finalize = ctx.artifacts.get("finalize_decision") or {}
            decision = finalize.get("decision") or (ctx.artifacts.get("po_match") or {}).get("decision")
            status, data_quality = _run_status_and_quality(ctx.artifacts, finalize)
            await self._store.finish_run(
                run_id=run_id,
                tenant_id=tenant_id,
                status=status,
                decision=decision,
                credits_charged=credits_charged,
                data_quality=data_quality,
            )
            return {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "item_key": item_key,
                "skills_run": skills_run,
                "credits_charged": credits_charged,
                "decision": decision,
                "status": status,
                "data_quality": data_quality,
                "token_usage": token_usage_total,
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

    async def _charge(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        identify: str,
        credits: int,
        usage: Optional[dict[str, Any]] = None,
    ) -> str:
        try:
            result = await self._ezofis.charge_activity_credit(
                tenant_id=tenant_id,
                skill_id=skill_id,
                identify=identify,
                credit=credits,
                usage=usage,
            )
            if isinstance(result, dict) and result.get("status") == "failed":
                return "failed"
            return "charged"
        except Exception:
            logger.warning("ap_credit_charge_failed", extra={"skill_id": skill_id})
            return "failed"


__all__ = ["ApSkillRunner", "REGISTRY", "resolve_item_key"]
