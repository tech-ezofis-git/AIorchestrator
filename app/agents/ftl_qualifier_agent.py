"""The FTL RFQ Qualifier Agent — qualifies elevator-parts RFQs against the Wittur pricelist."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from app.ftl.page_text import (
    extract_pdf_text_hybrid,
    is_image_filename,
    ocr_image_file,
    prefer_spec_attachment,
)
from app.ftl.qualifier import extract, runs_store, skill_store
from app.ftl.qualifier import agent as qualifier_agent
from app.ftl.qualifier.output_format import to_public
from app.ftl.job_progress import FtlJobProgressReporter

logger = logging.getLogger("orchestrator.ftl_qualifier_agent")

DECISION_EMOJI = {"qualify": "✅", "disqualify": "❌", "needs_review": "🟡"}


def format_decision_markdown(run: Dict[str, Any]) -> str:
    if run.get("status") == "error":
        return f"### ⚠️ Run failed\n\n{run.get('error_detail', 'Unknown error')}"

    result = run.get("result") or run
    decision = run.get("decision") or result.get("qualify") or "unknown"
    emoji = DECISION_EMOJI.get(decision, "❓")
    confidence = run.get("confidence")
    if confidence is None:
        confidence = result.get("confidence")
    conf_str = ""
    if confidence is not None:
        try:
            number = float(confidence)
        except (TypeError, ValueError):
            number = None
        if number is not None:
            if number <= 1:
                number *= 100
            percent = int(round(max(0.0, min(100.0, number))))
            conf_str = f"  ·  **Decision confidence:** {percent}%"

    lines = [
        f"### {emoji} {decision.replace('_', ' ').upper()}",
    ]

    dup_info = run.get("duplicate_info")
    if dup_info and dup_info.get("is_duplicate"):
        matched_id = dup_info.get("matched_run_id")
        reason_txt = dup_info.get("reason", "Identical email content match.")
        lines.append(f"> ⚡ **Duplicate RFQ Detected** (Matches Run #{matched_id}): {reason_txt}")
    elif dup_info and dup_info.get("is_potential_duplicate"):
        matched_id = dup_info.get("matched_run_id")
        sim_pct = int(dup_info.get("similarity_score", 0) * 100)
        reason_txt = dup_info.get("reason", "")
        lines.append(f"> 🟡 **Possible Duplicate / Revision ({sim_pct}% match with Run #{matched_id})**: {reason_txt}")

    lines.append(f"**Project type:** {run.get('project_type') or result.get('project_type') or 'unknown'}{conf_str}")
    project_name = run.get("project_name") or result.get("project_name")
    if project_name:
        lines.append(f"**Project:** {project_name}")
    company_name = result.get("customer_name")
    if company_name:
        lines.append(f"**Company:** {company_name}")
    deadline = run.get("deadline_text") or result.get("deadline")
    if deadline:
        lines.append(f"**Deadline:** {deadline}")
    flags = result.get("flags") or []
    if flags:
        lines.append(f"**Flags:** {', '.join(flags)}")
    reasoning = result.get("reasoning")
    if reasoning:
        lines.append("")
        lines.append(f"**Reasoning:** {reasoning}")
    ai_insight = result.get("ai_insight")
    if ai_insight:
        lines.append(f"**AI insight:** {ai_insight}")
    matched = result.get("matched_items") or []
    if matched:
        lines.append("")
        lines.append("#### Matched Items")
        for it in matched:
            cat_ref = f" ({it.get('catalog_ref')})" if it.get("catalog_ref") else ""
            lines.append(f"- **{it.get('item', 'Item')}** [{it.get('category', '')}] {it.get('match', '')}{cat_ref}: {it.get('note', '')}")
    excluded = result.get("excluded_items") or []
    if excluded:
        lines.append("")
        lines.append("#### Excluded Items")
        for it in excluded:
            lines.append(f"- **{it.get('item', 'Item')}**: {it.get('reason', '')}")
    held = result.get("hold_items") or []
    if held:
        lines.append("")
        lines.append("#### In scope, not auto-quoted")
        for it in held:
            lines.append(f"- **{it.get('item', 'Item')}**: {it.get('reason', '')}")

    if run.get("id"):
        lines.append("")
        lines.append(f"_Run #{run['id']} · {run.get('total_tokens', 0)} tokens_")
    return "\n\n".join(lines)


class FtlQualifierAgent:
    """Agent that extracts RFQ specifications and qualifies whether FTL can bid."""

    def __init__(self, ezofis: Any = None, **kwargs: Any) -> None:
        self._ezofis = ezofis or kwargs.get("ezofis")

    async def _text_from_attachment(self, filename: str, content: bytes) -> str:
        att_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if att_ext == "pdf":
            return await extract_pdf_text_hybrid(content)
        if att_ext == "docx":
            return extract.extract_docx_text(content)
        if is_image_filename(filename):
            return await ocr_image_file(content, filename)
        return ""

    async def _build_candidate_from_bytes(
        self, file_bytes: bytes, filename: str
    ) -> Tuple[str, Dict[str, Any], str]:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        email_meta: Dict[str, Any] = {}

        if ext == "eml":
            from app.ftl.laya_comparator import parse_and_extract_eml

            eml_data = parse_and_extract_eml(file_bytes, filename=filename)
            email_meta = {
                "from": eml_data.get("from"),
                "to": eml_data.get("to"),
                "cc": eml_data.get("cc"),
                "subject": eml_data.get("subject"),
                "raw_subject": eml_data.get("raw_subject"),
                "date": eml_data.get("date"),
                "message_id": eml_data.get("message_id"),
                "body_text": eml_data.get("body_text"),
                "attachments_meta": eml_data.get("attachments_meta"),
                "content_fingerprint": eml_data.get("content_fingerprint"),
                "normalized_body": eml_data.get("normalized_body"),
            }

            attachments = eml_data.get("attachments") or []
            spec_attachment = prefer_spec_attachment(attachments)
            if spec_attachment:
                full_text = await self._text_from_attachment(
                    spec_attachment.get("filename") or "",
                    spec_attachment["bytes"],
                )
            else:
                full_text = eml_data.get("body_text", "")
        elif ext == "pdf":
            full_text = await extract_pdf_text_hybrid(file_bytes)
        elif ext == "docx":
            full_text = extract.extract_docx_text(file_bytes)
        elif is_image_filename(filename):
            full_text = await ocr_image_file(file_bytes, filename)
        else:
            full_text = file_bytes.decode("utf-8", errors="replace")

        candidate = extract.build_candidate_text(full_text, email_meta=email_meta or None)
        rendered = extract.render_candidate_text_for_model(candidate, email_meta=email_meta or None)
        return rendered, email_meta, ext

    async def qualify(
        self,
        *,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        filepath: Optional[str] = None,
        candidate_text: Optional[str] = None,
        raw_text: Optional[str] = None,
        model_override: Optional[str] = None,
        llm_overrides: Optional[Dict[str, Any]] = None,
        llm_fallback_overrides: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
        ap_agent_job_id: Optional[str] = None,
        ezofis: Any = None,
    ) -> Dict[str, Any]:
        """Runs qualification on the given file/text asynchronously in a worker thread."""
        progress = FtlJobProgressReporter(
            ezofis=ezofis,
            job_id=ap_agent_job_id,
            tenant_id=tenant_id,
        )

        input_filename = filename or (os.path.basename(filepath) if filepath else "manual_input")
        input_type = "text"
        email_meta: Dict[str, Any] = {}
        content_fingerprint: Optional[str] = None
        normalized_content: Optional[str] = None
        duplicate_info: Optional[Dict[str, Any]] = None

        try:
            # --- 20%: Reading the RFQ ---
            await progress.update("PROCESSING", "Reading the RFQ", 20)

            if file_bytes is not None:
                rendered, email_meta, input_type = await self._build_candidate_from_bytes(file_bytes, input_filename)
                content_fingerprint = email_meta.get("content_fingerprint")
                normalized_content = email_meta.get("normalized_body")
            elif filepath is not None and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    fb = f.read()
                rendered, email_meta, input_type = await self._build_candidate_from_bytes(fb, input_filename)
                content_fingerprint = email_meta.get("content_fingerprint")
                normalized_content = email_meta.get("normalized_body")
            elif candidate_text:
                rendered = candidate_text
            elif raw_text:
                candidate = extract.build_candidate_text(raw_text)
                rendered = extract.render_candidate_text_for_model(candidate)
            else:
                raise ValueError("No RFQ content provided (must provide file_bytes, filepath, or text).")

            # --- Duplicate Detection Check (Content-Based) ---
            if input_type == "eml" or content_fingerprint:
                from app.ftl.laya_comparator import LayaContentComparator, parse_and_extract_eml

                # If we have file bytes or candidate info, check against existing runs
                eml_repr = {
                    "from": email_meta.get("from", ""),
                    "to": email_meta.get("to", []),
                    "subject": email_meta.get("subject", ""),
                    "normalized_body": normalized_content or rendered,
                    "content_fingerprint": content_fingerprint,
                    "attachments_meta": email_meta.get("attachments_meta", []),
                }

                past_runs = runs_store.load_runs()
                comparator = LayaContentComparator()
                dup_result = comparator.find_duplicate_in_runs(
                    eml_repr,
                    past_runs,
                    llm_overrides=_request_llm_overrides if False else llm_overrides,
                )
                duplicate_info = dup_result.to_dict()

                # If EXACT DUPLICATE detected: prevent repeated qualification and return existing result
                if dup_result.is_duplicate and dup_result.match_type == "exact_hash" and dup_result.matched_run_id:
                    matched_run = runs_store.get_run(dup_result.matched_run_id)
                    if matched_run and matched_run.get("result"):
                        logger.info(
                            "Exact duplicate EML detected matching Run #%s (fingerprint=%s)",
                            dup_result.matched_run_id,
                            content_fingerprint,
                        )
                        await progress.update(
                            "COMPLETED",
                            f"Exact duplicate RFQ detected (matches Run #{dup_result.matched_run_id})",
                            100,
                        )
                        cached_decision = dict(matched_run.get("result") or {})
                        cached_decision["duplicate_info"] = duplicate_info
                        return {
                            "decision": cached_decision,
                            "run_record": matched_run,
                            "total_tokens": 0,
                            "candidate_text": rendered,
                            "duplicate_info": duplicate_info,
                        }

            # --- 40%: Extracting RFQ requirements ---
            await progress.update("PROCESSING", "Extracting RFQ requirements", 40)

            skill = await skill_store.load_runtime_skill(tenant_id=tenant_id)
            overrides = dict(llm_overrides or {})
            if model_override:
                overrides["model"] = model_override

            # --- 60%: Matching RFQ items with Wittur catalog ---
            await progress.update("PROCESSING", "Matching RFQ items with Wittur catalog", 60)

            # --- 80%: Applying qualification rules (LLM call runs here) ---
            await progress.update("PROCESSING", "Applying qualification rules", 80)

            loop = asyncio.get_running_loop()

            def _on_progress(msg: str, pct: int) -> None:
                if progress.enabled:
                    asyncio.run_coroutine_threadsafe(
                        progress.update("PROCESSING", msg, pct),
                        loop,
                    )

            def _run() -> Tuple[Dict[str, Any], int]:
                fn = qualifier_agent.run_qualification
                import inspect
                sig = inspect.signature(fn)
                kwargs: Dict[str, Any] = {"llm_overrides": overrides or None}
                if "fallback_overrides" in sig.parameters:
                    kwargs["fallback_overrides"] = llm_fallback_overrides or None
                if "progress_callback" in sig.parameters:
                    kwargs["progress_callback"] = _on_progress
                return fn(skill, rendered, **kwargs)

            decision, total_tokens = await asyncio.to_thread(_run)

            # If semantic duplicate or possible duplicate for review was identified, add review flag
            if duplicate_info and duplicate_info.get("is_potential_duplicate"):
                matched_id = duplicate_info.get("matched_run_id")
                sim_pct = int(duplicate_info.get("similarity_score", 0) * 100)
                reason_txt = duplicate_info.get("reason") or "Potential duplicate email detected."
                flags = decision.setdefault("flags", [])
                if isinstance(flags, list):
                    flags.append(f"Duplicate Review [{sim_pct}% match with Run #{matched_id}]: {reason_txt}")

            # --- 90%: Preparing qualification result ---
            await progress.update("PROCESSING", "Preparing qualification result", 90)

            # Record run
            raw_file_bytes = file_bytes
            if raw_file_bytes is None and filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    raw_file_bytes = f.read()

            run_record = runs_store.append_run(
                input_filename=input_filename,
                input_type=input_type,
                candidate_text=rendered,
                decision=decision,
                total_tokens=total_tokens,
                raw_file_bytes=raw_file_bytes,
                content_fingerprint=content_fingerprint,
                normalized_content=normalized_content,
                email_metadata=email_meta,
                duplicate_info=duplicate_info,
            )

            # --- 100%: Qualification completed ---
            await progress.update("COMPLETED", "RFQ qualification completed successfully", 100)

            return {
                "decision": decision,
                "run_record": run_record,
                "total_tokens": total_tokens,
                "candidate_text": rendered,
                "duplicate_info": duplicate_info,
            }

        except Exception as exc:
            # Report failure to the Hangfire job before re-raising.
            # Setting 100% on FAILED ensures Hangfire and the client progress loader
            # complete their cycle cleanly with the error message.
            await progress.update("FAILED", f"Qualification failed: {exc}", 100)
            raise

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        document_job: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Entry point for /chat and AgentRouter."""
        job = document_job or {}
        file_bytes = job.get("file_bytes")
        filename = job.get("filename")
        filepath = job.get("filepath")
        candidate_text = job.get("candidate_text")
        raw_text = job.get("raw_text") or (message if not file_bytes and not filepath and not candidate_text else None)
        model = job.get("model")
        raw_job_id = (
            job.get("ap_agent_job_id")
            or job.get("apAgentJobId")
            or job.get("apJobId")
            or job.get("job_id")
            or job.get("jobId")
            or job.get("JobId")
        )
        ap_agent_job_id = str(raw_job_id).strip() if raw_job_id else None
        tenant_id = (
            job.get("tenant_id")
            or job.get("tenantId")
            or job.get("tenantid")
            or job.get("TenantId")
        )
        ezofis = kwargs.get("ezofis") or self._ezofis

        try:
            res = await self.qualify(
                file_bytes=file_bytes,
                filename=filename,
                filepath=filepath,
                candidate_text=candidate_text,
                raw_text=raw_text,
                model_override=model,
                llm_overrides=job.get("llm_overrides"),
                llm_fallback_overrides=job.get("llm_fallback_overrides"),
                tenant_id=tenant_id,
                ap_agent_job_id=ap_agent_job_id,
                ezofis=ezofis,
            )
            run_rec = dict(res["run_record"] or {})
            if res.get("duplicate_info"):
                run_rec["duplicate_info"] = res["duplicate_info"]
            reply_md = format_decision_markdown(run_rec)
            decision_dict = dict(res.get("decision") or {})
            if res.get("duplicate_info") and not decision_dict.get("duplicate_info"):
                decision_dict["duplicate_info"] = res["duplicate_info"]
            return {
                "reply": reply_md,
                "usage": {"total_tokens": res["total_tokens"]},
                "qualifier_result": to_public(decision_dict),
                "run_id": run_rec.get("id"),
                "run_record": run_rec,
            }
        except Exception as exc:
            logger.exception("ftl_qualifier_error", extra={"error": str(exc), "error_type": type(exc).__name__})
            return {
                "reply": f"### ⚠️ Qualification Failed\n\n{str(exc)}",
                "usage": None,
                "error": str(exc),
            }
