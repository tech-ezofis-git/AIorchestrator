"""Human-readable AP validation text for agent_data_validation / AIAGENTResponse."""
from __future__ import annotations

from typing import Any, Optional

from app.ap_skills.types import field_number, field_text, match_lines_by_description


def resolve_source_type(artifacts: dict[str, Any], document_job: Optional[dict[str, Any]] = None) -> str:
    """Where PO/vendor master data was validated.

    Labels written to agent_data_validation as ``source_type``.
    """
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    job = document_job if isinstance(document_job, dict) else {}

    for skill_id, label in (
        ("po_lookup_sap", None),
        ("po_lookup_quickbooks", "QuickBooks"),
        ("po_lookup_sage", "Sage"),
    ):
        art = artifacts.get(skill_id) or {}
        if not isinstance(art, dict) or art.get("skipped"):
            continue
        if not (art.get("po") or art.get("lookup_error") or art.get("po_number")):
            continue
        if skill_id == "po_lookup_sap":
            return _sap_or_hana_label(art, job)
        return label or skill_id

    po_match = artifacts.get("po_match") or {}
    po = po_match.get("po") if isinstance(po_match, dict) else None
    if isinstance(po, dict) and po:
        source = str(po.get("source") or "").strip().lower()
        if "hana" in source:
            return "HANA Cloud"
        if source.startswith("sap") or source == "sap_sample":
            return "SAP"
        if source in {"quickbooks", "qb"}:
            return "QuickBooks"
        if source == "sage":
            return "Sage"
        if po.get("ezfb_table") or po.get("form_id") or source in {"", "ezofis", "form", "master"}:
            return "EZOFIS DB"
        return "EZOFIS DB"

    if str(job.get("resource") or "").strip().upper() in {"SAP", "HANA"} or job.get("connector_id"):
        # Lookup was requested but produced no PO — still report intended master.
        resource = str(job.get("resource") or "").strip().upper()
        if "HANA" in resource:
            return "HANA Cloud"
        if resource.startswith("SAP") or resource in {"S4", "S/4"}:
            return "SAP"
    return "Not validated"


def _sap_or_hana_label(art: dict[str, Any], job: dict[str, Any]) -> str:
    backend = str(art.get("source") or "").strip().lower()
    po = art.get("po") if isinstance(art.get("po"), dict) else {}
    po_source = str(po.get("source") or "").strip().lower()
    resource = str(job.get("resource") or "").strip().upper()
    if (
        backend == "hana"
        or "hana" in po_source
        or "HANA" in resource
        or str(art.get("reason") or "").upper().find("HANA") >= 0
    ):
        return "HANA Cloud"
    return "SAP"


def build_ai_insight(
    *,
    decision: str,
    po_match: Optional[dict[str, Any]] = None,
    vendor: Optional[dict[str, Any]] = None,
) -> str:
    """Short verifier-facing recommendation stored as ``ai_insight``."""
    decision_u = str(decision or "").strip().upper()
    po_match = po_match if isinstance(po_match, dict) else {}
    vendor = vendor if isinstance(vendor, dict) else {}
    reason_bits = str(po_match.get("reason") or "").lower()
    vendor_ok = str(vendor.get("status") or "").upper() == "ACTIVE" or "vendor matches" in reason_bits
    totals_ok = "totals match" in reason_bits
    po_found = bool(po_match.get("po")) or (
        "po " in reason_bits and "found" in reason_bits and "not found" not in reason_bits
    )

    if decision_u == "MATCHED":
        if vendor_ok and totals_ok:
            return "PO vendor totals and line amounts match - approve for posting"
        return "Invoice matched to purchase order - approve for posting"
    if decision_u == "PARTIALLY_MATCHED":
        if po_found and not vendor_ok:
            return "PO found but vendor differs — send to verifier review"
        if po_found and not totals_ok:
            return "PO found but amounts differ — send to verifier review"
        return "Partial PO match — send to verifier before posting"
    if decision_u in {"NOT_MATCHED", "DUPLICATE"}:
        if decision_u == "DUPLICATE":
            return "Possible duplicate invoice — do not post until cleared"
        if not po_found:
            return "Purchase order not found — hold for manual review"
        return "PO match failed — hold for manual review"
    if decision_u == "NON_INVOICE":
        return "Document is not an AP invoice — route as Non-Invoice"
    return "AP validation complete — review decision before posting"


def build_validation_reason(
    *,
    decision: str,
    base_reason: str,
    artifacts: dict[str, Any],
    invoice: Optional[dict[str, Any]] = None,
) -> str:
    """Longer narrative for ``reason`` on agent_data_validation."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    invoice = invoice if isinstance(invoice, dict) else {}
    po_match = artifacts.get("po_match") if isinstance(artifacts.get("po_match"), dict) else {}
    vendor = artifacts.get("vendor_validate") if isinstance(artifacts.get("vendor_validate"), dict) else {}
    parts: list[str] = []

    score = po_match.get("score")
    if isinstance(score, (int, float)):
        parts.append(f"The overall matching score is {float(score):.0f}%.")

    vendor_status = str(vendor.get("status") or "").upper()
    po_reason = str(po_match.get("reason") or "").strip()
    po_reason_l = po_reason.lower()
    vendor_ok = vendor_status == "ACTIVE" or "vendor matches" in po_reason_l
    headers_ok = "totals match" in po_reason_l or "within tolerance" in po_reason_l

    if vendor_status == "MISMATCH":
        inv_v = vendor.get("vendor") or field_text(invoice, "vendor", "supplier")
        exp_v = vendor.get("expected")
        if inv_v and exp_v:
            parts.append(f"Invoice vendor '{inv_v}' does not match PO vendor '{exp_v}'.")
        else:
            parts.append("The vendor does not match the purchase order vendor.")
    elif vendor_status == "MISSING":
        parts.append("No vendor name was found on the invoice.")
    elif vendor_ok and headers_ok:
        parts.append("The vendor was identified, and key header fields align within tolerance.")
    elif vendor_ok:
        parts.append("The vendor was identified and matches the PO vendor.")
    elif po_reason:
        parts.append(po_reason)

    line_note = _line_match_summary(invoice, po_match.get("po") if isinstance(po_match.get("po"), dict) else {})
    if line_note:
        parts.append(line_note)

    base = str(base_reason or "").strip()
    narrative = " ".join(p for p in parts if p).strip()
    if not narrative:
        return base
    if base and base not in narrative and not narrative.startswith(base[:40]):
        # Prefer the richer narrative; keep base if it adds unique context (e.g. duplicate cap).
        if "capped:" in base.lower() or "duplicate" in base.lower():
            return f"{narrative} ({base})"
    return narrative


def _line_match_summary(invoice: dict[str, Any], po: dict[str, Any]) -> str:
    inv_lines = invoice.get("line_items") or invoice.get("lines") or []
    if not isinstance(inv_lines, list) or not inv_lines:
        return ""
    if not isinstance(po, dict) or not po:
        return ""
    po_lines = po.get("lines") or po.get("PO Line Item Mapped") or po.get("PO Line Item") or []
    if isinstance(po_lines, str):
        return ""
    if not isinstance(po_lines, list) or not po_lines:
        return ""

    def _desc(row: dict[str, Any]) -> str:
        return field_text(row, "description", "Description") or ""

    inv_dicts = [r for r in inv_lines if isinstance(r, dict)]
    po_dicts = [r for r in po_lines if isinstance(r, dict)]
    if not inv_dicts or not po_dicts:
        return ""

    paired = match_lines_by_description(inv_dicts, po_dicts, describe=_desc)
    matched = sum(1 for idx in paired if idx is not None)
    total = len(inv_dicts)
    qty_mismatch = 0
    for inv_i, po_i in enumerate(paired):
        if po_i is None:
            continue
        inv_qty = field_number(inv_dicts[inv_i], "qty", "Quantity", "quantity")
        po_qty = field_number(po_dicts[po_i], "qty", "Quantity", "quantity", "orderQuantity")
        if inv_qty is not None and po_qty is not None and abs(float(inv_qty) - float(po_qty)) > 0.001:
            qty_mismatch += 1

    if matched == 0:
        return f"0/{total} invoice lines matched to the PO."
    if qty_mismatch:
        return (
            f"{matched}/{total} lines matched, but the quantity for "
            f"{qty_mismatch} line item{'s' if qty_mismatch != 1 else ''} does not match the PO."
        )
    if matched == total:
        return f"{matched}/{total} lines matched the PO."
    return f"{matched}/{total} lines matched the PO."


def enrichment_for_finalize(
    *,
    decision: str,
    reason: str,
    artifacts: dict[str, Any],
    invoice: dict[str, Any],
    document_job: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    po_match = artifacts.get("po_match") if isinstance(artifacts.get("po_match"), dict) else {}
    vendor = artifacts.get("vendor_validate") if isinstance(artifacts.get("vendor_validate"), dict) else {}
    return {
        "ai_insight": build_ai_insight(decision=decision, po_match=po_match, vendor=vendor),
        "reason": build_validation_reason(
            decision=decision,
            base_reason=reason,
            artifacts=artifacts,
            invoice=invoice,
        ),
        "source_type": resolve_source_type(artifacts, document_job),
    }
