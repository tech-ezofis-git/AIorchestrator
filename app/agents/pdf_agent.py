"""The PDF Generator agent — converts structured JSON into beautiful, styled PDFs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import Settings
from app.llm.adapter import LLMAdapter
from app.pdf_skills.pdf_generator import generate_pdf_from_json, PdfGenerationResult

logger = logging.getLogger("orchestrator.pdf_agent")

_STATIC_PDF_DIR = Path(__file__).resolve().parent.parent / "static" / "generated_pdfs"


class PdfAgent:
    def __init__(
        self,
        llm_adapter: Optional[LLMAdapter] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._llm = llm_adapter
        self._settings = settings
        _STATIC_PDF_DIR.mkdir(parents=True, exist_ok=True)

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: Optional[list[dict[str, str]]] = None,
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Handles PDF generation requests from POST /chat."""
        job = document_job or {}
        template_json = (
            job.get("pdf_schema")
            or job.get("schema")
            or job.get("schema_json")
            or job.get("template_json")
            or job.get("templateJson")
            or job.get("pdfTemplate")
            or job.get("pdf_template")
        )
        pdf_json = (
            job.get("pdf_json")
            or job.get("formData")
            or job.get("form_data")
            or job.get("data")
            or job.get("json_data")
        )
        pdf_title = job.get("pdf_title") or job.get("title")
        pdf_theme = job.get("pdf_theme") or job.get("theme")
        template_name = (
            job.get("template_name")
            or job.get("template_id")
            or job.get("template")
            or job.get("templateName")
        )

        # If not in document_job, check if message is a JSON string
        if not pdf_json and not template_json and message:
            trimmed = message.strip()
            # If message contains JSON (e.g. enclosed in ```json ... ``` or raw {...})
            if trimmed.startswith("{") or trimmed.startswith("["):
                try:
                    parsed_msg = json.loads(trimmed)
                    if isinstance(parsed_msg, dict) and "schemas" in parsed_msg:
                        template_json = parsed_msg
                        pdf_json = parsed_msg.get("data") or parsed_msg.get("formData") or parsed_msg
                    else:
                        pdf_json = parsed_msg
                except Exception:
                    pass
            elif "```json" in trimmed:
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", trimmed)
                if match:
                    try:
                        parsed_msg = json.loads(match.group(1).strip())
                        if isinstance(parsed_msg, dict) and "schemas" in parsed_msg:
                            template_json = parsed_msg
                            pdf_json = parsed_msg.get("data") or parsed_msg.get("formData") or parsed_msg
                        else:
                            pdf_json = parsed_msg
                    except Exception:
                        pass

        # If pdf_json itself is a schema definition
        if isinstance(pdf_json, dict) and "schemas" in pdf_json and not template_json:
            template_json = pdf_json
            pdf_json = pdf_json.get("data") or pdf_json.get("formData") or pdf_json

        # If we have a schema template but no explicit form data, form data is empty dict
        if template_json and not pdf_json:
            pdf_json = template_json.get("data") or template_json.get("formData") or {}

        # If still no JSON, and we have an LLM adapter + a natural language message,
        # synthesize structured JSON from the user description
        usage = None
        if not pdf_json and not template_json and message and self._llm:
            try:
                system_prompt = (
                    "You are the PDF data structuring assistant for EZOFIS. "
                    "Extract or generate structured JSON from the user's description. "
                    "Include appropriate title, metadata fields, sections, and items/tables. "
                    "Return ONLY valid JSON (no markdown formatting, no commentary)."
                )
                llm_res = await self._llm.chat_completion(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message},
                    ]
                )
                raw_text = str(llm_res.get("content") or "").strip()
                if "```" in raw_text:
                    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
                    if match:
                        raw_text = match.group(1).strip()
                parsed_synth = json.loads(raw_text)
                if isinstance(parsed_synth, dict) and "schemas" in parsed_synth:
                    template_json = parsed_synth
                    pdf_json = parsed_synth.get("data") or parsed_synth.get("formData") or parsed_synth
                else:
                    pdf_json = parsed_synth
                usage = llm_res.get("usage")
            except Exception as exc:
                logger.warning("pdf_llm_synthesis_failed", extra={"error": str(exc)})

        if not pdf_json and not template_json:
            raise ValueError(
                "PDF generation requires a JSON object with values in payload.pdf_json, "
                "a PDF schema JSON in payload.pdf_schema/templateJson, or a valid JSON string in message."
            )

        if pdf_json is not None and not isinstance(pdf_json, (dict, list)):
            raise ValueError("pdf_json must be a JSON object or list of records.")

        # Determine target file path inside static directory
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        candidate_title = pdf_title or (pdf_json.get("title") if isinstance(pdf_json, dict) else "Document")
        clean_title = re.sub(r"[^\w\-.]", "_", str(candidate_title or "document")).strip("_") or "document"
        out_filename = f"{clean_title}_{timestamp}.pdf"
        out_path = str(_STATIC_PDF_DIR / out_filename)

        # Run CPU-bound ReportLab generation in async thread pool
        gen_result: PdfGenerationResult = await asyncio.to_thread(
            generate_pdf_from_json,
            pdf_json or {},
            template_name=template_name,
            template_json=template_json,
            output_path=out_path,
            title=pdf_title,
            theme=pdf_theme,
        )

        reply = (
            f"PDF document '{gen_result.filename}' generated successfully "
            f"({gen_result.page_count} page{'s' if gen_result.page_count != 1 else ''}, "
            f"{gen_result.file_size_bytes / 1024:.1f} KB)."
        )

        res_dict = gen_result.to_dict()
        res_dict["download_url"] = f"/api/pdf/download/{gen_result.filename}"
        res_dict["preview_url"] = f"/api/pdf/preview/{gen_result.filename}"
        res_dict["static_url"] = f"/static/generated_pdfs/{gen_result.filename}"

        return {
            "reply": reply,
            "usage": usage,
            "pdf_result": res_dict,
        }
