"""Resolve Workflow PoMaster form id from designer workflowJson (AP_AGENT block)."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def workflow_json_from_api(value: Any) -> Optional[dict[str, Any]]:
    """Designer document, a JSON string, or a GET /workflows body."""
    doc = _as_dict(value)
    if doc is None:
        return None
    blocks = doc.get("blocks") if isinstance(doc.get("blocks"), list) else doc.get("Blocks")
    if isinstance(blocks, list):
        return doc
    for key in ("workflowJson", "workflow_json", "WorkflowJson"):
        if doc.get(key) not in (None, ""):
            inner = workflow_json_from_api(doc.get(key))
            if inner is not None:
                return inner
    data = doc.get("data")
    if isinstance(data, (dict, str)):
        inner = workflow_json_from_api(data)
        if inner is not None:
            return inner
    return None


def extract_po_master_from_workflow_json(
    workflow_json: Any,
) -> tuple[Optional[str], Optional[str]]:
    """
    Return (master_source, master_form_id) from the first AP_AGENT block.

    Mirrors Core ``WorkflowApAgentJson.TryReadPoMaster`` so Agents can recover when
    Hangfire omits ``master_form_id``.
    """
    root = _as_dict(workflow_json)
    if root is None:
        return None, None

    blocks = root.get("blocks")
    if blocks is None:
        blocks = root.get("Blocks")
    if not isinstance(blocks, list):
        return None, None

    for block in blocks:
        b = _as_dict(block)
        if b is None:
            continue
        btype = str(b.get("type") or b.get("Type") or "").strip().upper()
        settings = _as_dict(b.get("settings") or b.get("Settings")) or {}
        ap_agent = _as_dict(settings.get("apAgent") or settings.get("ApAgent")) or {}
        if btype not in ("AP_AGENT", "APAGENT") and not ap_agent:
            continue

        source_raw = (
            settings.get("poMasterSourceType")
            or settings.get("PoMasterSourceType")
            or settings.get("masterSource")
            or ap_agent.get("poMasterSourceType")
            or ap_agent.get("resource")
            or ap_agent.get("Resource")
        )
        # Designer stores the PO master on apAgent.formId. Block settings.formId
        # is often the invoice write-back form; using that makes po_match refuse.
        form_id = (
            ap_agent.get("formId")
            or ap_agent.get("FormId")
            or ap_agent.get("masterFormId")
            or ap_agent.get("master_form_id")
            or settings.get("masterFormId")
            or settings.get("MasterFormId")
            or settings.get("formId")
            or settings.get("FormId")
        )
        form_id_s = str(form_id or "").strip() or None
        source_s = str(source_raw or "").strip()
        if not source_s and form_id_s:
            source_s = "InternalForm"
        elif source_s.lower() in ("internal", "form", "ezofis", "internalform"):
            source_s = "InternalForm"
        elif not source_s:
            continue
        return source_s, form_id_s

    return None, None


def _invoice_form_id(document_job: dict[str, Any], invoice_form_id: Optional[str] = None) -> str:
    return str(
        invoice_form_id
        or document_job.get("form_id")
        or document_job.get("formid")
        or document_job.get("formId")
        or ""
    ).strip()


def _master_form_id(document_job: dict[str, Any]) -> str:
    return str(
        document_job.get("master_form_id") or document_job.get("masterFormId") or ""
    ).strip()


def master_form_id_is_usable(
    document_job: dict[str, Any],
    invoice_form_id: Optional[str] = None,
) -> bool:
    """False when PoMaster is missing or is the invoice write-back form."""
    if not isinstance(document_job, dict):
        return False
    master = _master_form_id(document_job)
    if not master:
        return False
    invoice = _invoice_form_id(document_job, invoice_form_id)
    return not invoice or master.lower() != invoice.lower()


def ensure_master_form_id_on_job(
    document_job: dict[str, Any],
    workflow_json: Any,
    invoice_form_id: Optional[str] = None,
) -> bool:
    """Stamp master_form_id / master_source from workflow JSON.

    Replaces a master id that is missing or equal to the invoice form.
    Returns True if stamped.
    """
    if not isinstance(document_job, dict):
        return False
    if master_form_id_is_usable(document_job, invoice_form_id=invoice_form_id):
        return False
    source, form_id = extract_po_master_from_workflow_json(workflow_json_from_api(workflow_json))
    if not form_id:
        return False
    invoice = _invoice_form_id(document_job, invoice_form_id)
    if invoice and form_id.lower() == invoice.lower():
        return False
    document_job["master_form_id"] = form_id
    if source and not str(document_job.get("master_source") or document_job.get("masterSource") or "").strip():
        document_job["master_source"] = source
    return True


async def recover_master_form_id(
    document_job: dict[str, Any],
    *,
    ezofis: Any,
    tenant_id: str,
    invoice_form_id: Optional[str] = None,
) -> str:
    """PO lookup id for every tenant: workflow apAgent.formId, never the invoice form.

    Uses a job master_form_id that is already distinct from the invoice form.
    Otherwise loads the designer workflow and stamps that master.
    """
    if not isinstance(document_job, dict):
        return ""
    if master_form_id_is_usable(document_job, invoice_form_id=invoice_form_id):
        return _master_form_id(document_job)

    wf_id = str(
        document_job.get("workflow_id") or document_job.get("workflowId") or ""
    ).strip()
    invoice = _invoice_form_id(document_job, invoice_form_id)
    extra = {
        "workflow_id": wf_id,
        "invoice_form_id": invoice,
        "master_form_id": _master_form_id(document_job),
    }
    getter = getattr(ezofis, "get_workflow", None) if ezofis is not None else None
    if not wf_id or getter is None:
        logger.warning(
            "ap_master_form_id_unresolved reason=%s workflow_id=%s",
            "missing_workflow_id" if not wf_id else "no_workflow_client",
            wf_id,
            extra={**extra, "reason": "missing_workflow_id" if not wf_id else "no_workflow_client"},
        )
        return ""
    wf = await getter(tenant_id=tenant_id, workflow_id=wf_id)
    wj = workflow_json_from_api(wf)
    if not wj:
        status = wf.get("status_code") if isinstance(wf, dict) else None
        logger.warning(
            "ap_master_form_id_unresolved reason=workflow_json_missing workflow_id=%s status=%s",
            wf_id,
            status,
            extra={**extra, "reason": "workflow_json_missing", "status_code": status},
        )
        return ""
    source, _form = extract_po_master_from_workflow_json(wj)
    if source and _norm_master_source(source) not in {"", "INTERNALFORM", "INTERNAL", "FORM", "EZOFIS"}:
        document_job["master_source"] = source
    if ensure_master_form_id_on_job(document_job, wj, invoice_form_id=invoice_form_id):
        logger.info(
            "ap_master_form_id_resolved_from_workflow",
            extra={
                "workflow_id": wf_id,
                "master_form_id": document_job.get("master_form_id"),
                "invoice_form_id": invoice,
            },
        )
        return _master_form_id(document_job)
    logger.warning(
        "ap_master_form_id_unresolved reason=workflow_master_is_invoice_form workflow_id=%s",
        wf_id,
        extra={**extra, "reason": "workflow_master_is_invoice_form"},
    )
    return ""


def _norm_master_source(value: str) -> str:
    return (
        (value or "")
        .strip()
        .upper()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )
