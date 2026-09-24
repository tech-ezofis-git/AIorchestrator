"""FTL Qualifier and Quote Estimator REST routes."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from app.agents.ftl_qualifier_agent import FtlQualifierAgent
from app.agents.ftl_quote_estimator_agent import FtlQuoteEstimatorAgent
from app.ftl.qualifier import (
    pricelist_store as qualifier_pricelist_store,
    runs_store as qualifier_runs_store,
    skill_store as qualifier_skill_store,
)
from app.ftl.quote_estimator import (
    pricelist_store as quote_pricelist_store,
    quote_pdf as quote_pdf_module,
    quotes_store as quote_quotes_store,
    skill_store as quote_skill_store,
)

logger = logging.getLogger("orchestrator.ftl.api")

router = APIRouter(tags=["ftl"])


def _qualifier_result_from(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        return value or None
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) and parsed else None
    return None


def _request_llm_overrides(request: Request, model: Optional[str]) -> dict[str, Any] | None:
    """Same frozen preset the chat path stores on document_job['llm_overrides']."""
    adapter = getattr(request.app.state, "llm_adapter", None)
    snapshot = adapter.snapshot_overrides() if adapter is not None and hasattr(adapter, "snapshot_overrides") else {}
    overrides = dict(snapshot or {})
    if isinstance(model, str) and model.strip():
        overrides["model"] = model.strip()
    return overrides or None


@router.post("/api/ftl/qualify")
async def ftl_qualify(request: Request) -> dict[str, Any]:
    """Qualify an elevator-parts RFQ against the Wittur pricelist."""
    agent: FtlQualifierAgent = request.app.state.ftl_qualifier_agent
    content_type = (request.headers.get("content-type") or "").lower()

    file_b: Optional[bytes] = None
    f_name: Optional[str] = None
    f_path: Optional[str] = None
    cand_text: Optional[str] = None
    r_text: Optional[str] = None
    m_override: Optional[str] = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_file = form.get("file")
        if isinstance(uploaded_file, UploadFile):
            file_b = await uploaded_file.read()
            f_name = uploaded_file.filename
        f_name = (form.get("filename") if isinstance(form.get("filename"), str) else None) or f_name
        f_path = form.get("filepath") if isinstance(form.get("filepath"), str) else None
        cand_text = form.get("candidate_text") if isinstance(form.get("candidate_text"), str) else None
        r_text = form.get("raw_text") if isinstance(form.get("raw_text"), str) else None
        m_override = form.get("model") if isinstance(form.get("model"), str) else None
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            f_name = body.get("filename")
            f_path = body.get("filepath")
            cand_text = body.get("candidate_text") or body.get("candidateText")
            r_text = body.get("raw_text") or body.get("rawText") or body.get("message")
            m_override = body.get("model")
            b64_bytes = body.get("file_bytes") or body.get("fileBytes")
            if b64_bytes and isinstance(b64_bytes, str):
                import base64

                try:
                    file_b = base64.b64decode(b64_bytes)
                except Exception:
                    pass

    try:
        res = await agent.qualify(
            file_bytes=file_b,
            filename=f_name,
            filepath=f_path,
            candidate_text=cand_text,
            raw_text=r_text,
            model_override=m_override,
            llm_overrides=_request_llm_overrides(request, m_override),
        )
        return {
            "status": "success",
            "decision": res["decision"],
            "run_id": res["run_record"].get("id"),
            "run_record": res["run_record"],
            "total_tokens": res["total_tokens"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ftl_qualify_endpoint_failed")
        raise HTTPException(status_code=500, detail=f"FTL qualify failed: {str(exc)}") from exc


@router.get("/api/ftl/qualifier/skill")
async def get_qualifier_skill() -> dict[str, Any]:
    return qualifier_skill_store.get_skill()


@router.put("/api/ftl/qualifier/skill")
async def update_qualifier_skill(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if "instructions" in payload:
        qualifier_skill_store.save_instructions(str(payload["instructions"]))
    if "references" in payload and isinstance(payload["references"], list):
        for ref in payload["references"]:
            if isinstance(ref, dict) and "id" in ref:
                qualifier_skill_store.save_reference(ref["id"], ref.get("title", ""), ref.get("content", ""))
    return qualifier_skill_store.get_skill()


@router.post("/api/ftl/qualifier/pricelist/reindex")
async def reindex_qualifier_pricelist(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded pricelist file is empty.")
    try:
        return qualifier_pricelist_store.reindex_pricelist(content, file.filename or "pricelist.pdf")
    except Exception as exc:
        logger.exception("ftl_qualifier_reindex_failed")
        raise HTTPException(status_code=500, detail=f"Reindex failed: {str(exc)}") from exc


@router.get("/api/ftl/qualifier/pricelist/status")
async def get_qualifier_pricelist_status() -> dict[str, Any]:
    return qualifier_pricelist_store.status()


@router.get("/api/ftl/qualifier/runs")
async def list_qualifier_runs() -> list[dict[str, Any]]:
    return qualifier_runs_store.list_runs()


@router.get("/api/ftl/qualifier/runs/{run_id}")
async def get_qualifier_run(run_id: str) -> dict[str, Any]:
    run = qualifier_runs_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.post("/api/ftl/quote")
async def ftl_quote(request: Request) -> dict[str, Any]:
    agent: FtlQuoteEstimatorAgent = request.app.state.ftl_quote_estimator_agent
    content_type = (request.headers.get("content-type") or "").lower()

    file_b: Optional[bytes] = None
    f_name: Optional[str] = None
    f_path: Optional[str] = None
    cand_text: Optional[str] = None
    r_text: Optional[str] = None
    qualifier_result: Optional[dict[str, Any]] = None
    tpl_type: str = "inflow"
    m_override: Optional[str] = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_file = form.get("file")
        if isinstance(uploaded_file, UploadFile):
            file_b = await uploaded_file.read()
            f_name = uploaded_file.filename
        f_name = (form.get("filename") if isinstance(form.get("filename"), str) else None) or f_name
        f_path = form.get("filepath") if isinstance(form.get("filepath"), str) else None
        cand_text = form.get("candidate_text") if isinstance(form.get("candidate_text"), str) else None
        r_text = form.get("raw_text") if isinstance(form.get("raw_text"), str) else None
        qualifier_result = _qualifier_result_from(form.get("qualifier_result") or form.get("qualifierResult"))
        tpl_val = form.get("template_type") or form.get("templateType")
        if isinstance(tpl_val, str) and tpl_val.strip():
            tpl_type = tpl_val.strip()
        m_override = form.get("model") if isinstance(form.get("model"), str) else None
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            f_name = body.get("filename")
            f_path = body.get("filepath")
            cand_text = body.get("candidate_text") or body.get("candidateText")
            r_text = body.get("raw_text") or body.get("rawText") or body.get("message")
            qualifier_result = _qualifier_result_from(body.get("qualifier_result") or body.get("qualifierResult"))
            tpl_type = body.get("template_type") or body.get("templateType") or tpl_type
            m_override = body.get("model")
            b64_bytes = body.get("file_bytes") or body.get("fileBytes")
            if b64_bytes and isinstance(b64_bytes, str):
                import base64

                try:
                    file_b = base64.b64decode(b64_bytes)
                except Exception:
                    pass

    try:
        res = await agent.estimate(
            file_bytes=file_b,
            filename=f_name,
            filepath=f_path,
            candidate_text=cand_text,
            raw_text=r_text,
            qualifier_result=qualifier_result,
            template_type=tpl_type,
            model_override=m_override,
            llm_overrides=_request_llm_overrides(request, m_override),
        )
        return {
            "status": "success",
            "estimate_number": res["estimate_number"],
            "quote_result": res["quote_result"],
            "rendered_html": res["rendered_html"],
            "pdf_download_url": res["pdf_download_url"],
            "total_tokens": res["total_tokens"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ftl_quote_endpoint_failed")
        raise HTTPException(status_code=500, detail=f"FTL quote estimation failed: {str(exc)}") from exc


@router.post("/api/ftl/base64-to-pdf")
async def base64_to_pdf(payload: dict[str, Any] = Body(...)) -> Response:
    """Turn a pdf_base64 string (e.g. from the /chat estimator reply) into a viewable / downloadable PDF."""
    raw = str(payload.get("pdf_base64") or payload.get("pdfBase64") or "").strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    raw = "".join(raw.split())
    if not raw:
        raise HTTPException(status_code=400, detail="pdf_base64 is required.")
    try:
        pdf_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="pdf_base64 is not valid base64.") from exc
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Decoded data is not a PDF.")

    filename = str(payload.get("filename") or payload.get("pdf_filename") or "quote.pdf").strip()
    filename = filename.replace("/", "-").replace("\\", "-").replace('"', "") or "quote.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    disposition = "attachment" if payload.get("download") else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.post("/api/ftl/quote/pdf")
async def render_quote_pdf(payload: dict[str, Any] = Body(...)) -> Response:
    """Build a PDF from an estimator JSON (e.g. an edited quote_result), without re-running the model."""
    quote = payload.get("quote_result") or payload.get("quoteResult")
    if isinstance(quote, str) and quote.strip():
        try:
            quote = json.loads(quote)
        except json.JSONDecodeError:
            quote = None
    if not isinstance(quote, dict) or not quote:
        raise HTTPException(status_code=400, detail="quote_result JSON is required.")

    template_type = str(payload.get("template_type") or payload.get("templateType") or "inflow").strip() or "inflow"
    estimate_number = str(quote.get("estimate_number") or payload.get("estimate_number") or "ESTIMATE").strip()

    try:
        pdf_bytes = quote_pdf_module.generate_quote_pdf(quote, estimate_number, template_type=template_type)
    except Exception as exc:
        logger.exception("ftl_quote_pdf_render_failed")
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(exc)}") from exc

    safe_name = estimate_number.replace("/", "-").replace("\\", "-").replace(" ", "_")
    suffix = "" if template_type.lower() in ("inflow", "standard") else "_internal_review"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_name}{suffix}.pdf"'},
    )


@router.get("/api/ftl/quote/pdf/{estimate_number}")
async def download_quote_pdf(
    estimate_number: str,
    template_type: str = Query("inflow", description="Template style: inflow or internal_review"),
) -> Response:
    quote_record = quote_quotes_store.get_quote(estimate_number)
    safe_name = str(estimate_number).replace("/", "-").replace("\\", "-").replace(" ", "_")
    suffix = "" if template_type == "inflow" else "_internal_review"
    pdf_filename = f"{safe_name}{suffix}.pdf"
    pdf_dir = os.path.join(os.path.dirname(__file__), "quote_estimator", "data", "pdf_downloads")
    pdf_path = os.path.join(pdf_dir, pdf_filename)

    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    elif quote_record and quote_record.get("result"):
        try:
            pdf_bytes = quote_pdf_module.generate_quote_pdf(
                quote_record["result"], estimate_number, template_type=template_type
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(exc)}") from exc
    else:
        raise HTTPException(status_code=404, detail="Quote not found.")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf_filename}"'},
    )


@router.get("/api/ftl/quote/skill")
async def get_quote_skill() -> dict[str, Any]:
    return quote_skill_store.get_skill()


@router.put("/api/ftl/quote/skill")
async def update_quote_skill(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if "instructions" in payload:
        quote_skill_store.save_instructions(str(payload["instructions"]))
    if "references" in payload and isinstance(payload["references"], list):
        for ref in payload["references"]:
            if isinstance(ref, dict) and "id" in ref:
                quote_skill_store.save_reference(ref["id"], ref.get("title", ""), ref.get("content", ""))
    return quote_skill_store.get_skill()


@router.post("/api/ftl/quote/pricelist/reindex")
async def reindex_quote_pricelist(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded pricelist file is empty.")
    try:
        return quote_pricelist_store.reindex_pricelist(content, file.filename or "pricelist.pdf")
    except Exception as exc:
        logger.exception("ftl_quote_reindex_failed")
        raise HTTPException(status_code=500, detail=f"Reindex failed: {str(exc)}") from exc


@router.get("/api/ftl/quote/pricelist/status")
async def get_quote_pricelist_status() -> dict[str, Any]:
    return quote_pricelist_store.status()


@router.get("/api/ftl/quote/quotes")
async def list_quotes() -> list[dict[str, Any]]:
    return quote_quotes_store.list_quotes()


@router.get("/api/ftl/quote/quotes/{estimate_number}")
async def get_quote_detail(estimate_number: str) -> dict[str, Any]:
    quote = quote_quotes_store.get_quote(estimate_number)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote estimate not found.")
    return quote
