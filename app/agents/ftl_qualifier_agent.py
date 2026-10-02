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
        f"**Project type:** {run.get('project_type') or result.get('project_type') or 'unknown'}{conf_str}",
    ]
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
            parsed = extract.parse_eml_bytes(file_bytes)
            email_meta = {k: v for k, v in parsed.items() if k != "attachments"}
            attachments = parsed.get("attachments") or []
            spec_attachment = prefer_spec_attachment(attachments)
            if spec_attachment:
                full_text = await self._text_from_attachment(
                    spec_attachment.get("filename") or "",
                    spec_attachment["bytes"],
                )
            else:
                full_text = parsed.get("body_text", "")
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

        try:
            # --- 20%: Reading the RFQ ---
            await progress.update("PROCESSING", "Reading the RFQ", 20)

            if file_bytes is not None:
                rendered, _, input_type = await self._build_candidate_from_bytes(file_bytes, input_filename)
            elif filepath is not None and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    fb = f.read()
                rendered, _, input_type = await self._build_candidate_from_bytes(fb, input_filename)
            elif candidate_text:
                rendered = candidate_text
            elif raw_text:
                candidate = extract.build_candidate_text(raw_text)
                rendered = extract.render_candidate_text_for_model(candidate)
            else:
                raise ValueError("No RFQ content provided (must provide file_bytes, filepath, or text).")

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
            )

            # --- 100%: Qualification completed ---
            await progress.update("COMPLETED", "RFQ qualification completed successfully", 100)

            return {
                "decision": decision,
                "run_record": run_record,
                "total_tokens": total_tokens,
                "candidate_text": rendered,
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
            run_rec = res["run_record"]
            reply_md = format_decision_markdown(run_rec)
            return {
                "reply": reply_md,
                "usage": {"total_tokens": res["total_tokens"]},
                "qualifier_result": to_public(res["decision"]),
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
