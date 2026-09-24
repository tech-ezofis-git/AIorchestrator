"""Resolve Workflow PoMaster form id from designer workflowJson (AP_AGENT block)."""
from __future__ import annotations

from typing import Any, Optional


def _as_dict(value: Any) -> Optional[dict[str, Any]]:
    return value if isinstance(value, dict) else None


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


def _invoice_form_id(document_job: dict[str, Any]) -> str:
    return str(
        document_job.get("form_id")
        or document_job.get("formid")
        or document_job.get("formId")
        or ""
    ).strip()


def master_form_id_is_usable(document_job: dict[str, Any]) -> bool:
    """False when PoMaster is missing or is the invoice write-back form."""
    if not isinstance(document_job, dict):
        return False
    master = str(
        document_job.get("master_form_id") or document_job.get("masterFormId") or ""
    ).strip()
    if not master:
        return False
    invoice = _invoice_form_id(document_job)
    return not invoice or master.lower() != invoice.lower()


def ensure_master_form_id_on_job(document_job: dict[str, Any], workflow_json: Any) -> bool:
    """Stamp master_form_id / master_source from workflow JSON.

    Replaces a master id that is missing or equal to the invoice form.
    Returns True if stamped.
    """
    if not isinstance(document_job, dict):
        return False
    if master_form_id_is_usable(document_job):
        return False
    source, form_id = extract_po_master_from_workflow_json(workflow_json)
    if not form_id:
        return False
    invoice = _invoice_form_id(document_job)
    if invoice and form_id.lower() == invoice.lower():
        return False
    document_job["master_form_id"] = form_id
    if source and not str(document_job.get("master_source") or document_job.get("masterSource") or "").strip():
        document_job["master_source"] = source
    return True
