"""The FTL Quote Estimator Agent — builds priced Sales Estimates for elevator-parts RFQs."""
from __future__ import annotations

import asyncio
import base64
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
from app.ftl.quote_estimator import (
    extract,
    quotes_store,
    skill_store,
    agent as quote_agent,
)
from app.ftl.quote_estimator.quote_pdf import generate_quote_pdf
from app.ftl.quote_estimator.quote_template import compute_totals, render_quote_html

logger = logging.getLogger("orchestrator.ftl_quote_estimator_agent")


def render_qualifier_decision_for_quote(decision: Dict[str, Any]) -> str:
    """Turn an edited qualifier decision into the text the quote model already prices."""
    lines = [
        "This quote request is an edited FTL qualifier decision, not a raw spec file.",
        "Price every item under Matched items. Do not add a line item for anything under Excluded items.",
        f"Project name: {decision.get('project_name') or ''}",
        f"Project type: {decision.get('project_type') or ''}",
        f"Deadline: {decision.get('deadline') or ''}",
        f"Qualify decision: {decision.get('qualify') or ''}",
    ]
    flags = decision.get("flags") or []
    if isinstance(flags, list) and flags:
        lines.append("Flags: " + "; ".join(str(flag) for flag in flags))
    lines.append("")
    lines.append("## Matched items (in scope — price these)")
    matched = decision.get("matched_items") or []
    if isinstance(matched, list) and matched:
        for item in matched:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- item: {item}; category: {category}; match: {match}; catalog_ref: {catalog_ref}; note: {note}".format(
                    item=item.get("item") or "",
                    category=item.get("category") or "",
                    match=item.get("match") or "",
                    catalog_ref=item.get("catalog_ref") or "",
                    note=item.get("note") or "",
                )
            )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Excluded items (out of scope — do not price these)")
    excluded = decision.get("excluded_items") or []
    if isinstance(excluded, list) and excluded:
        for item in excluded:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- item: {item}; reason: {reason}".format(
                    item=item.get("item") or "",
                    reason=item.get("reason") or "",
                )
            )
    else:
        lines.append("(none)")
    reasoning = (decision.get("reasoning") or "").strip()
    if reasoning:
        lines.append("")
        lines.append("## Qualifier reasoning")
        lines.append(reasoning)
    return "\n".join(lines).strip()

PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ftl", "quote_estimator", "data", "pdf_downloads")


def quote_pdf_filename(estimate_number: str, template_type: Optional[str]) -> str:
    safe_name = str(estimate_number).replace("/", "-").replace("\\", "-").replace(" ", "_")
    norm_tpl = str(template_type or "inflow").strip().lower()
    suffix = "" if norm_tpl in ("inflow", "standard") else "_internal_review"
    return f"{safe_name}{suffix}.pdf"


def render_pdf_from_quote(quote: Dict[str, Any], template_type: Optional[str] = "inflow") -> Dict[str, Any]:
    """Render an (edited) estimator quote_result to a PDF without calling the model."""
    estimate_number = str(quote.get("estimate_number") or "ESTIMATE").strip() or "ESTIMATE"
    template_type = template_type or "inflow"
    pdf_bytes = generate_quote_pdf(quote, estimate_number, template_type=template_type)
    totals = compute_totals(quote)
    updated = dict(quote)
    updated["estimate_number"] = estimate_number
    updated["subtotal"] = totals.get("subtotal", 0.0)
    updated["freight"] = totals.get("freight", 0.0)
    updated["hst"] = totals.get("hst", 0.0)
    updated["total"] = totals.get("total", 0.0)
    if totals.get("line_items"):
        updated["line_items"] = totals["line_items"]
    return {
        "quote_result": updated,
        "estimate_number": estimate_number,
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "pdf_filename": quote_pdf_filename(estimate_number, template_type),
    }


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
        qualifier_result: Optional[Dict[str, Any]] = None,
        template_type: str = "inflow",
        model_override: Optional[str] = None,
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Runs quote estimation asynchronously and persists the result."""
        input_filename = filename or (os.path.basename(filepath) if filepath else "manual_input")
        input_type = "text"

        if isinstance(qualifier_result, dict) and qualifier_result:
            rendered = render_qualifier_decision_for_quote(qualifier_result)
            input_filename = input_filename if filename else "qualifier_result.json"
            input_type = "qualifier_json"
        elif file_bytes is not None:
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
            return quote_agent.run_quote_estimation(skill, rendered, llm_overrides=overrides or None)

        quote_result, total_tokens = await asyncio.to_thread(_run)

        estimate_number = quote_result.get("estimate_number") or quotes_store.next_estimate_number()
        quote_result["estimate_number"] = estimate_number

        # Render HTML
        rendered_html = render_quote_html(quote_result, estimate_number, template_type=template_type)

        # Write PDF to downloads cache
        pdf_bytes: Optional[bytes] = None
        try:
            pdf_bytes = generate_quote_pdf(quote_result, estimate_number, template_type=template_type)
            pdf_filename = quote_pdf_filename(estimate_number, template_type)
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
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii") if pdf_bytes else None,
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
        qualifier_result = job.get("qualifier_result")
        raw_text = job.get("raw_text") or (
            message if not file_bytes and not filepath and not candidate_text and not qualifier_result else None
        )
        template_type = job.get("template_type") or job.get("quote_template_type") or "inflow"
        model = job.get("model")
        quote_input = job.get("quote_result")

        if isinstance(quote_input, dict) and quote_input:
            try:
                res = await asyncio.to_thread(render_pdf_from_quote, quote_input, template_type)
            except Exception as exc:
                logger.exception("ftl_quote_pdf_render_error")
                return {
                    "reply": f"### ⚠️ Quote PDF Failed\n\n{str(exc)}",
                    "usage": None,
                    "error": str(exc),
                }
            return {
                "reply": f"PDF generated for {res['estimate_number']} ({template_type}).",
                "usage": None,
                "quote_result": res["quote_result"],
                "estimate_number": res["estimate_number"],
                "pdf_base64": res["pdf_base64"],
                "pdf_filename": res["pdf_filename"],
            }

        try:
            res = await self.estimate(
                file_bytes=file_bytes,
                filename=filename,
                filepath=filepath,
                candidate_text=candidate_text,
                raw_text=raw_text,
                qualifier_result=qualifier_result if isinstance(qualifier_result, dict) else None,
                template_type=template_type,
                model_override=model,
                llm_overrides=job.get("llm_overrides"),
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
                "pdf_base64": res.get("pdf_base64"),
                "pdf_filename": res.get("pdf_filename"),
                "quote_record": res["quote_record"],
            }
        except Exception as exc:
            logger.exception("ftl_quote_estimator_error")
            return {
                "reply": f"### ⚠️ Quote Estimation Failed\n\n{str(exc)}",
                "usage": None,
                "error": str(exc),
            }
