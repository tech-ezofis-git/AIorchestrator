"""The FTL Quote Estimator Agent — builds priced Sales Estimates for elevator-parts RFQs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from app.ftl.quote_estimator import (
    extract,
    quotes_store,
    skill_store,
    agent as quote_agent,
)
from app.ftl.quote_estimator.quote_pdf import generate_quote_pdf
from app.ftl.quote_estimator.quote_template import compute_totals, render_quote_html

logger = logging.getLogger("orchestrator.ftl_quote_estimator_agent")

PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ftl", "quote_estimator", "data", "pdf_downloads")


def format_quote_markdown(quote: Dict[str, Any], estimate_number: str) -> str:
    lines = [
        f"### 📋 Sales Estimate: {estimate_number}",
        f"**Project:** {quote.get('project_name') or 'N/A'}",
        f"**Customer:** {quote.get('customer_name') or 'N/A'}",
    ]
    if quote.get("elevator_id"):
        lines.append(f"**Elevator / Car ID:** {quote['elevator_id']}")
    if quote.get("quote_date"):
        lines.append(f"**Date:** {quote['quote_date']}")

    items = quote.get("line_items") or []
    if items:
        lines.append("")
        lines.append("#### Line Items")
        for it in items:
            code = it.get("product_code") or ""
            desc = it.get("description") or ""
            qty = it.get("qty") if it.get("qty") is not None else (it.get("quantity") if it.get("quantity") is not None else 1)
            unit_p = it.get("unit_price") or 0.0
            sub_over = it.get("subtotal_override")
            sub_tot = it.get("subtotal")
            total_p = sub_tot if sub_tot is not None else (sub_over if sub_over is not None else (qty * unit_p))
            lines.append(f"- **{code}**: {desc} (Qty: {qty}, Unit: ${unit_p:,.2f}, Ext: ${total_p:,.2f})")

    totals = compute_totals(quote)
    lines.append("")
    lines.append("#### Totals Summary")
    lines.append(f"- **Subtotal:** ${totals.get('subtotal', 0.0):,.2f}")
    lines.append(f"- **Freight:** ${totals.get('freight', 0.0):,.2f}")
    lines.append(f"- **HST (13%):** ${totals.get('hst', 0.0):,.2f}")
    lines.append(f"- **Grand Total:** ${totals.get('total', 0.0):,.2f}")

    notes = quote.get("notes") or []
    if notes:
        lines.append("")
        lines.append("#### Notes & Conditions")
        for n in notes:
            lines.append(f"- {n}")

    return "\n\n".join(lines)


class FtlQuoteEstimatorAgent:
    """Agent that extracts RFQ specifications and calculates priced quotes with HTML/PDF rendering."""

    def __init__(self, **kwargs: Any) -> None:
        os.makedirs(PDF_DIR, exist_ok=True)

    def _build_candidate_from_bytes(
        self, file_bytes: bytes, filename: str
    ) -> Tuple[str, Dict[str, Any], str]:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        email_meta: Dict[str, Any] = {}
        if ext == "eml":
            parsed = extract.parse_eml_bytes(file_bytes)
            email_meta = {k: v for k, v in parsed.items() if k != "attachments"}
            attachments = parsed.get("attachments") or []
            spec_attachment = next(
                (
                    a
                    for a in attachments
                    if (a.get("filename") or "").lower().rsplit(".", 1)[-1]
                    in ("pdf", "docx")
                ),
                None,
            )
            if spec_attachment:
                att_ext = (spec_attachment.get("filename") or "").lower().rsplit(".", 1)[-1]
                full_text = (
                    extract.extract_pdf_text(spec_attachment["bytes"])
                    if att_ext == "pdf"
                    else extract.extract_docx_text(spec_attachment["bytes"])
                )
            else:
                full_text = parsed.get("body_text", "")
        elif ext == "pdf":
            full_text = extract.extract_pdf_text(file_bytes)
        elif ext == "docx":
            full_text = extract.extract_docx_text(file_bytes)
        else:
            full_text = file_bytes.decode("utf-8", errors="replace")

        candidate = extract.build_candidate_text(full_text)
        rendered = extract.render_candidate_text_for_model(candidate, email_meta=email_meta or None)
        return rendered, email_meta, ext

    async def estimate(
        self,
        *,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        filepath: Optional[str] = None,
        candidate_text: Optional[str] = None,
        raw_text: Optional[str] = None,
        template_type: str = "inflow",
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Runs quote estimation asynchronously and persists the result."""
        input_filename = filename or (os.path.basename(filepath) if filepath else "manual_input")
        input_type = "text"

        if file_bytes is not None:
            rendered, _, input_type = self._build_candidate_from_bytes(file_bytes, input_filename)
        elif filepath is not None and os.path.exists(filepath):
            with open(filepath, "rb") as f:
                fb = f.read()
            rendered, _, input_type = self._build_candidate_from_bytes(fb, input_filename)
        elif candidate_text:
            rendered = candidate_text
        elif raw_text:
            candidate = extract.build_candidate_text(raw_text)
            rendered = extract.render_candidate_text_for_model(candidate)
        else:
            raise ValueError("No RFQ content provided (must provide file_bytes, filepath, or text).")

        skill = skill_store.get_skill()

        def _run() -> Tuple[Dict[str, Any], int]:
            if model_override:
                prev_model = os.environ.get("QUOTE_CHAT_MODEL")
                os.environ["QUOTE_CHAT_MODEL"] = model_override
                try:
                    return quote_agent.run_quote_estimation(skill, rendered)
                finally:
                    if prev_model is not None:
                        os.environ["QUOTE_CHAT_MODEL"] = prev_model
                    else:
                        os.environ.pop("QUOTE_CHAT_MODEL", None)
            return quote_agent.run_quote_estimation(skill, rendered)

        quote_result, total_tokens = await asyncio.to_thread(_run)

        estimate_number = quote_result.get("estimate_number") or quotes_store.next_estimate_number()
        quote_result["estimate_number"] = estimate_number

        # Render HTML
        rendered_html = render_quote_html(quote_result, estimate_number, template_type=template_type)

        # Write PDF to downloads cache
        try:
            pdf_bytes = generate_quote_pdf(quote_result, estimate_number, template_type=template_type)
            safe_name = str(estimate_number).replace("/", "-").replace("\\", "-").replace(" ", "_")
            norm_tpl = str(template_type or "inflow").strip().lower()
            suffix = "" if norm_tpl in ("inflow", "standard") else "_internal_review"
            pdf_filename = f"{safe_name}{suffix}.pdf"
            pdf_path = os.path.join(PDF_DIR, pdf_filename)
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            pdf_available = True
        except Exception as pdf_err:
            logger.warning(f"Failed to generate quote PDF: {pdf_err}")
            pdf_available = False
            pdf_filename = None

        totals = compute_totals(quote_result)
        quote_result["subtotal"] = totals.get("subtotal", 0.0)
        quote_result["freight"] = totals.get("freight", 0.0)
        quote_result["hst"] = totals.get("hst", 0.0)
        quote_result["total"] = totals.get("total", 0.0)
        if totals.get("line_items"):
            quote_result["line_items"] = totals["line_items"]

        # Store quote
        raw_file_bytes = file_bytes
        if raw_file_bytes is None and filepath and os.path.exists(filepath):
            with open(filepath, "rb") as f:
                raw_file_bytes = f.read()

        quote_record = quotes_store.append_quote(
            estimate_number=estimate_number,
            input_filename=input_filename,
            input_type=input_type,
            candidate_text=rendered,
            quote_result=quote_result,
            rendered_html=rendered_html,
            total_tokens=total_tokens,
            raw_file_bytes=raw_file_bytes,
        )

        return {
            "quote_result": quote_result,
            "estimate_number": estimate_number,
            "rendered_html": rendered_html,
            "pdf_available": pdf_available,
            "pdf_filename": pdf_filename,
            "pdf_download_url": f"/api/ftl/quote/pdf/{estimate_number}?template_type={template_type}",
            "quote_record": quote_record,
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
        template_type = job.get("template_type") or job.get("quote_template_type") or "inflow"
        model = job.get("model")

        try:
            res = await self.estimate(
                file_bytes=file_bytes,
                filename=filename,
                filepath=filepath,
                candidate_text=candidate_text,
                raw_text=raw_text,
                template_type=template_type,
                model_override=model,
            )
            quote = res["quote_result"]
            est_num = res["estimate_number"]
            reply_md = format_quote_markdown(quote, est_num)
            return {
                "reply": reply_md,
                "usage": {"total_tokens": res["total_tokens"]},
                "quote_result": quote,
                "estimate_number": est_num,
                "rendered_html": res["rendered_html"],
                "pdf_download_url": res["pdf_download_url"],
                "quote_record": res["quote_record"],
            }
        except Exception as exc:
            logger.exception("ftl_quote_estimator_error")
            return {
                "reply": f"### ⚠️ Quote Estimation Failed\n\n{str(exc)}",
                "usage": None,
                "error": str(exc),
            }
