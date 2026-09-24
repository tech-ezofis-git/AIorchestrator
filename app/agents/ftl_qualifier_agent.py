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

logger = logging.getLogger("orchestrator.ftl_qualifier_agent")

DECISION_EMOJI = {"qualify": "✅", "disqualify": "❌", "needs_review": "🟡"}


def format_decision_markdown(run: Dict[str, Any]) -> str:
    if run.get("status") == "error":
        return f"### ⚠️ Run failed\n\n{run.get('error_detail', 'Unknown error')}"

    result = run.get("result") or run
    decision = run.get("decision") or result.get("qualify") or "unknown"
    emoji = DECISION_EMOJI.get(decision, "❓")
    confidence = run.get("confidence") or result.get("confidence")
    conf_str = f"  ·  **Confidence:** {round(float(confidence) * 100)}%" if confidence is not None else ""

    lines = [
        f"### {emoji} {decision.replace('_', ' ').upper()}",
        f"**Project type:** {run.get('project_type') or result.get('project_type') or 'unknown'}{conf_str}",
    ]
    project_name = run.get("project_name") or result.get("project_name")
    if project_name:
        lines.append(f"**Project:** {project_name}")
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

    def __init__(self, **kwargs: Any) -> None:
        pass

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
            email_meta = {
                "from": parsed.get("from", ""),
                "subject": parsed.get("subject", ""),
                "date": parsed.get("date", ""),
            }
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

        candidate = extract.build_candidate_text(full_text)
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
    ) -> Dict[str, Any]:
        """Runs qualification on the given file/text asynchronously in a worker thread."""
        input_filename = filename or (os.path.basename(filepath) if filepath else "manual_input")
        input_type = "text"

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

        skill = skill_store.get_skill()
        overrides = dict(llm_overrides or {})
        if model_override:
            overrides["model"] = model_override

        def _run() -> Tuple[Dict[str, Any], int]:
            return qualifier_agent.run_qualification(skill, rendered, llm_overrides=overrides or None)

        decision, total_tokens = await asyncio.to_thread(_run)

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

        return {
            "decision": decision,
            "run_record": run_record,
            "total_tokens": total_tokens,
            "candidate_text": rendered,
        }

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

        try:
            res = await self.qualify(
                file_bytes=file_bytes,
                filename=filename,
                filepath=filepath,
                candidate_text=candidate_text,
                raw_text=raw_text,
                model_override=model,
                llm_overrides=job.get("llm_overrides"),
            )
            run_rec = res["run_record"]
            reply_md = format_decision_markdown(run_rec)
            return {
                "reply": reply_md,
                "usage": {"total_tokens": res["total_tokens"]},
                "qualifier_result": res["decision"],
                "run_id": run_rec.get("id"),
                "run_record": run_rec,
            }
        except Exception as exc:
            logger.exception("ftl_qualifier_error")
            return {
                "reply": f"### ⚠️ Qualification Failed\n\n{str(exc)}",
                "usage": None,
                "error": str(exc),
            }
