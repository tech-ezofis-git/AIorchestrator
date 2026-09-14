"""Build apagentv6-shaped AIAGENTResponse for agent_data_validation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from app.ap_skills.agent_validation_text import enrichment_for_finalize, resolve_source_type
from app.ap_skills.types import field_number, field_text, match_lines_by_description, name_similarity

_REVIEW_LABELS = {
    "MATCHED": "Matched",
    "PARTIALLY_MATCHED": "Partially Matched",
    "NOT_MATCHED": "Not Matched",
    "NON_INVOICE": "Non-Invoice",
    "DUPLICATE": "Not Matched",
}


def build_aiagent_response(
    *,
    decision: str,
    reason: str,
    artifacts: dict[str, Any],
    invoice: dict[str, Any],
    document_job: Optional[dict[str, Any]] = None,
    ai_insight: str = "",
    source_type: str = "",
) -> dict[str, Any]:
    """Full Agent validation JSON persisted on ``AIAGENTResponse``."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    invoice = invoice if isinstance(invoice, dict) else {}
    job = document_job if isinstance(document_job, dict) else {}

    finalize = artifacts.get("finalize_decision") if isinstance(artifacts.get("finalize_decision"), dict) else {}
    po_match = artifacts.get("po_match") if isinstance(artifacts.get("po_match"), dict) else {}
    vendor = artifacts.get("vendor_validate") if isinstance(artifacts.get("vendor_validate"), dict) else {}
    backorder = artifacts.get("backorder_detect") if isinstance(artifacts.get("backorder_detect"), dict) else {}
    duplicate = artifacts.get("duplicate_detect") if isinstance(artifacts.get("duplicate_detect"), dict) else {}
    matter = artifacts.get("matter_validate") if isinstance(artifacts.get("matter_validate"), dict) else {}

    decision_u = str(decision or finalize.get("decision") or "").strip().upper()
    review = _REVIEW_LABELS.get(decision_u, str(decision or "Not Matched").strip() or "Not Matched")
    if review not in ("Matched", "Partially Matched", "Not Matched", "Non-Invoice"):
        review = "Not Matched"

    base_reason = str(reason or finalize.get("reason") or "").strip()
    if not ai_insight or not source_type or not base_reason:
        enriched = enrichment_for_finalize(
            decision=decision_u or review,
            reason=base_reason,
            artifacts=artifacts,
            invoice=invoice,
            document_job=job,
        )
        ai_insight = ai_insight or enriched["ai_insight"]
        source_type = source_type or enriched["source_type"]
        base_reason = base_reason or enriched["reason"]
    if not source_type:
        source_type = resolve_source_type(artifacts, job)

    score = po_match.get("score")
    if not isinstance(score, (int, float)):
        score = finalize.get("score")
    if not isinstance(score, (int, float)):
        score = 0.0

    po = po_match.get("po") if isinstance(po_match.get("po"), dict) else {}
    fill = po_match.get("po_row") if isinstance(po_match.get("po_row"), dict) else {}
    po_row = _build_display_po_row(po, fill)

    response: dict[str, Any] = {
        "decision": review,
        "score": float(score),
        "ai_insight": ai_insight,
        "reason": base_reason,
        "source_type": source_type,
        "debug": {
            "Side-by-side Field Matching": _field_matching(invoice, po),
            "Side-by-side Line Item matching": _line_matching(invoice, po),
        },
        "po_row": po_row or None,
        "payment_terms": _payment_terms(invoice, po),
        "supplier_validation": _supplier_validation(vendor, invoice),
        "invoice_errors": _invoice_errors(duplicate, finalize),
        "back_order": _back_order(backorder, finalize),
        "Extracted Invoice JSON": _extracted_invoice_json(invoice, review),
        "matter_validation": _matter_validation(matter),
    }
    return {k: v for k, v in response.items() if v is not None}


def _field_score(inv: Any, po: Any, *, kind: str = "text") -> float:
    if inv in (None, "") and po in (None, ""):
        return 100.0
    if inv in (None, "") or po in (None, ""):
        return 0.0
    if kind == "number":
        try:
            a = float(str(inv).replace(",", "").replace("$", "").strip())
            b = float(str(po).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return 0.0
        if b == 0:
            return 100.0 if a == 0 else 0.0
        delta = abs(a - b) / abs(b)
        if delta <= 0.01:
            return 100.0
        if delta <= 0.05:
            return 80.0
        return 0.0
    sim = name_similarity(str(inv), str(po))
    return round(sim * 100.0, 2)


def _field_matching(invoice: dict[str, Any], po: dict[str, Any]) -> list[dict[str, Any]]:
    if not po:
        return []
    inv_total = field_number(invoice, "total", "amount", "Invoice Amount")
    po_total = field_number(po, "total", "amount", "PO Amount")
    pairs = (
        (
            "Supplier",
            field_text(invoice, "vendor", "supplier", "Vendor Name"),
            field_text(po, "vendor", "supplier", "Supplier", "Vendor Name", "Vendor"),
            "text",
        ),
        (
            "PO Number",
            field_text(invoice, "po_number", "PO Number", "poNumber"),
            field_text(po, "po_number", "PO Number", "poNumber")
            or field_text(invoice, "po_number", "PO Number"),
            "text",
        ),
        (
            "Currency",
            field_text(invoice, "currency", "Currency"),
            field_text(po, "currency", "Currency"),
            "text",
        ),
        (
            "Total Due",
            inv_total if inv_total is not None else field_text(invoice, "total", "amount", "Invoice Amount"),
            po_total if po_total is not None else field_text(po, "total", "amount", "PO Amount"),
            "number",
        ),
    )
    rows: list[dict[str, Any]] = []
    for label, inv_v, po_v, kind in pairs:
        if inv_v in (None, "") and po_v in (None, ""):
            continue
        rows.append(
            {
                "Field": label,
                "Invoice Value": _display(inv_v),
                "PO Value": _display(po_v),
                "Score": _field_score(inv_v, po_v, kind=kind),
            }
        )
    return rows


def _line_matching(invoice: dict[str, Any], po: dict[str, Any]) -> list[dict[str, Any]]:
    inv_lines = _invoice_lines(invoice)
    po_lines = _po_lines(po)
    if not inv_lines or not po_lines:
        return []

    def _desc(row: dict[str, Any]) -> str:
        return field_text(row, "description", "Description", "item", "name")

    paired = match_lines_by_description(inv_lines, po_lines, describe=_desc)
    out: list[dict[str, Any]] = []
    for inv_i, po_i in enumerate(paired):
        inv = inv_lines[inv_i]
        po_line = po_lines[po_i] if po_i is not None else {}
        inv_desc = field_text(inv, "description", "Description")
        po_desc = field_text(po_line, "description", "Description") if po_line else ""
        inv_qty = field_number(inv, "qty", "quantity", "Quantity")
        po_qty = (
            field_number(po_line, "qty", "quantity", "Quantity", "orderQuantity") if po_line else None
        )
        inv_qty_disp: Any = inv_qty if inv_qty is not None else (field_text(inv, "qty", "quantity", "Quantity") or "")
        po_qty_disp: Any = (
            po_qty
            if po_qty is not None
            else (field_text(po_line, "qty", "quantity", "Quantity") if po_line else "")
        )
        inv_price = field_number(inv, "price", "rate", "unit_price", "Unit Cost")
        po_price = field_number(po_line, "price", "rate", "unit_price", "Unit Cost") if po_line else None
        inv_amt = field_number(inv, "amount", "line_amount", "Extended")
        po_amt = field_number(po_line, "amount", "line_amount", "Extended") if po_line else None

        desc_score = _field_score(inv_desc, po_desc, kind="text") if po_line else 0.0
        qty_score = _field_score(
            inv_qty_disp if inv_qty_disp != "" else None,
            po_qty_disp if po_qty_disp != "" else None,
            kind="number",
        )
        price_score = _field_score(inv_price, po_price, kind="number")
        amt_score = _field_score(inv_amt, po_amt, kind="number")
        parts = [desc_score, qty_score, price_score, amt_score]
        line_score = round(sum(parts) / len(parts), 2) if po_line else 0.0
        out.append(
            {
                "Description": {
                    "Invoice Value": inv_desc or "",
                    "PO Value": po_desc or "",
                    "Score": desc_score,
                },
                "Quantity": {
                    "Invoice Value": _display(inv_qty_disp),
                    "PO Value": _display(po_qty_disp),
                    "Score": qty_score,
                },
                "Price": {
                    "Invoice Value": inv_price if inv_price is not None else "",
                    "PO Value": po_price if po_price is not None else "",
                    "Score": price_score,
                },
                "Amount": {
                    "Invoice Value": _display(inv_amt)
                    if inv_amt is not None
                    else field_text(inv, "amount", "line_amount") or "",
                    "PO Value": _display(po_amt)
                    if po_amt is not None
                    else (field_text(po_line, "amount", "line_amount") if po_line else ""),
                    "Score": amt_score,
                },
                "Line Score": line_score,
            }
        )
    return out


def _invoice_lines(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("line_items", "Line Item", "lines", "Line Items"):
        raw = invoice.get(key)
        if isinstance(raw, list) and raw:
            return [r for r in raw if isinstance(r, dict)]
    return []


def _po_lines(po: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(po, dict) or not po:
        return []
    for key in ("PO Line Item Mapped", "lines", "PO Line Item", "PO_Line_Item"):
        raw = po.get(key)
        if isinstance(raw, str) and raw.strip().startswith("["):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = None
        if isinstance(raw, list) and raw:
            return [r for r in raw if isinstance(r, dict)]
    return []


def _build_display_po_row(po: dict[str, Any], fill_row: dict[str, Any]) -> dict[str, Any]:
    if not po and not fill_row:
        return {}
    row: dict[str, Any] = {}
    if fill_row:
        row.update(fill_row)
    if not po:
        return row

    mapping = (
        ("PO Number", ("po_number", "PO Number", "poNumber")),
        ("Supplier", ("vendor", "supplier", "Supplier", "Vendor Name", "Vendor")),
        ("Supplier Address", ("supplier_address", "Supplier Address", "vendor_address", "Vendor Address")),
        ("Ship To Address", ("ship_to_address", "Ship To Address", "ship_to")),
        ("PO Date", ("po_date", "PO Date", "PO DATE")),
        ("Terms", ("terms", "Terms", "TERMS")),
        ("Due Date", ("due_date", "Due Date")),
        ("Buyer", ("buyer", "Buyer")),
        ("PO Amount", ("total", "amount", "PO Amount")),
        ("Currency", ("currency", "Currency")),
    )
    for label, keys in mapping:
        if row.get(label) not in (None, ""):
            continue
        text = field_text(po, *keys)
        if text:
            row[label] = text
            continue
        num = field_number(po, *keys)
        if num is not None:
            row[label] = int(num) if float(num).is_integer() else num

    if "PO Line Item Mapped" not in row:
        lines = _po_lines(po)
        if lines:
            cleaned: list[dict[str, Any]] = []
            for idx, line in enumerate(lines, start=1):
                item = {
                    "Line": str(field_text(line, "Line", "line", "id") or idx),
                    "Part Number": field_text(line, "Part Number", "part_number", "item_no", "sku") or None,
                    "description": field_text(line, "description", "Description") or None,
                    "UOM": field_text(line, "UOM", "uom") or None,
                    "Tax": field_text(line, "Tax", "tax") or None,
                    "quantity": _display(field_number(line, "quantity", "qty", "Quantity"))
                    if field_number(line, "quantity", "qty", "Quantity") is not None
                    else field_text(line, "quantity", "qty", "Quantity") or None,
                    "rate": _display(field_number(line, "rate", "price", "Unit Cost"))
                    if field_number(line, "rate", "price", "Unit Cost") is not None
                    else None,
                    "line_amount": _display(field_number(line, "line_amount", "amount", "Extended"))
                    if field_number(line, "line_amount", "amount", "Extended") is not None
                    else None,
                    "Req Date": field_text(line, "Req Date", "req_date", "delivery_date") or None,
                    "Weight": field_text(line, "weight", "Weight") or None,
                    "G/L Account": field_text(line, "G/L Account", "gl_account", "gl") or None,
                }
                cleaned.append({k: v for k, v in item.items() if v is not None})
            if cleaned:
                row["PO Line Item Mapped"] = cleaned

    for meta in (
        "itemId",
        "createdAt",
        "modifiedAt",
        "createdBy",
        "modifiedBy",
        "isDeleted",
        "todayTask",
        "isMarked",
        "PO Line Item",
        "source",
    ):
        if meta in po and meta not in row:
            row[meta] = po[meta]
    return row


def _payment_terms(invoice: dict[str, Any], po: dict[str, Any]) -> dict[str, Any]:
    raw = field_text(invoice, "terms", "Terms", "TERMS") or field_text(po, "terms", "Terms", "TERMS")
    invoice_date = field_text(invoice, "invoice_date", "Invoice Date")
    due_date = field_text(invoice, "due_date", "Due Date") or field_text(po, "due_date", "Due Date")
    normalized = _normalize_terms(raw)
    if normalized and invoice_date and not due_date and normalized.get("net_days"):
        parsed = _parse_date(invoice_date)
        if parsed:
            due_date = (parsed + timedelta(days=int(normalized["net_days"]))).date().isoformat()
    return {
        "raw": raw or None,
        "normalized": normalized,
        "invoice_date": invoice_date or None,
        "due_date": due_date or None,
    }


def _normalize_terms(raw: str) -> Optional[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return None
    lower = text.lower()
    if "net one month" in lower or "net 1 month" in lower:
        return {"basis": "NET", "net_days": 30, "discount": None}
    m = re.search(r"net\s*(\d+)", lower)
    if m:
        return {"basis": "NET", "net_days": int(m.group(1)), "discount": None}
    m = re.search(r"(\d+)\s*days?", lower)
    if m:
        return {"basis": "NET", "net_days": int(m.group(1)), "discount": None}
    return {"basis": "OTHER", "net_days": None, "discount": None}


def _parse_date(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _supplier_validation(vendor: dict[str, Any], invoice: dict[str, Any]) -> dict[str, Any]:
    status = vendor.get("status")
    reason = str(vendor.get("reason") or "").strip()
    inv_vendor = vendor.get("vendor") or field_text(invoice, "vendor", "supplier")
    expected = vendor.get("expected")
    mismatch: list[dict[str, Any]] = []
    if str(status or "").upper() == "MISMATCH" and inv_vendor and expected:
        mismatch.append({"invoice_vendor": inv_vendor, "po_vendor": expected})
    if str(status or "").upper() == "MISSING" and not reason:
        reason = "No vendor name found in invoice"
    if not inv_vendor and not reason:
        reason = "No vendor name found in invoice"
    return {
        "status": status if status not in (None, "", "MISSING") else None,
        "mismatch": mismatch,
        "vendor_master_match": True if vendor.get("source") == "vendor_master" else None,
        "validation_details": {"reason": reason or ""},
    }


def _invoice_errors(duplicate: dict[str, Any], finalize: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    severity = "NONE"
    if duplicate.get("is_duplicate_invoice") or finalize.get("duplicate"):
        severity = "DUPLICATE"
        errors.append(
            {
                "code": "DUPLICATE_INVOICE",
                "message": "Possible duplicate invoice",
                "possible_duplicate_of": duplicate.get("possible_duplicate_of")
                or finalize.get("possible_duplicate_of"),
            }
        )
    return {"severity": severity, "errors": errors}


def _back_order(backorder: dict[str, Any], finalize: dict[str, Any]) -> dict[str, Any]:
    if backorder:
        return {
            "detected": bool(backorder.get("detected")),
            "missing_qty_by_item": backorder.get("missing_qty_by_item") or [],
            "recommendation": backorder.get("recommendation")
            or ("REVIEW" if backorder.get("detected") else "NO_ACTION"),
        }
    return {
        "detected": bool(finalize.get("backorder")),
        "missing_qty_by_item": [],
        "recommendation": "NO_ACTION",
    }


def _extracted_invoice_json(invoice: dict[str, Any], review: str) -> dict[str, Any]:
    header_src = invoice.get("invoice_header") if isinstance(invoice.get("invoice_header"), dict) else invoice
    inv_amt = field_number(header_src, "total", "amount", "Invoice Amount")
    tax_amt = field_number(header_src, "tax", "tax_amount", "Invoice Tax Amount")
    header = {
        "Document Type": field_text(header_src, "doc_type", "Document Type", "document_type") or "INVOICE",
        "Invoice No": field_text(header_src, "invoice_number", "Invoice No", "invoice_no") or None,
        "PO Number": field_text(header_src, "po_number", "PO Number", "poNumber") or None,
        "PO DATE": field_text(header_src, "po_date", "PO Date", "PO DATE") or None,
        "Vendor Name": field_text(header_src, "vendor", "Vendor Name", "supplier", "Supplier") or None,
        "Currency": field_text(header_src, "currency", "Currency") or None,
        "TERMS": field_text(header_src, "terms", "Terms", "TERMS") or None,
        "PO Amount": field_text(header_src, "po_amount", "PO Amount") or None,
        "Invoice Date": field_text(header_src, "invoice_date", "Invoice Date") or None,
        "Due Date": field_text(header_src, "due_date", "Due Date") or None,
        "Invoice Amount": _display(inv_amt)
        if inv_amt is not None
        else field_text(header_src, "total", "amount", "Invoice Amount") or None,
        "Invoice Tax Amount": _display(tax_amt)
        if tax_amt is not None
        else field_text(header_src, "tax", "Invoice Tax Amount") or None,
        "Buyer": field_text(header_src, "buyer", "Buyer") or None,
        "Vendor Address": field_text(header_src, "vendor_address", "Vendor Address", "supplier_address")
        or None,
        "Ship To Address": field_text(header_src, "ship_to_address", "Ship To Address", "ship_to") or None,
        "Matched Status": review if review != "Non-Invoice" else None,
    }
    lines_out: list[dict[str, Any]] = []
    for idx, line in enumerate(_invoice_lines(invoice), start=1):
        qty = field_number(line, "quantity", "qty", "Quantity")
        rate = field_number(line, "rate", "price", "unit_price", "Unit Cost")
        amt = field_number(line, "line_amount", "amount", "Extended")
        lines_out.append(
            {
                "line_no": field_text(line, "line_no", "line", "Line") or idx,
                "item_no": field_text(line, "item_no", "part_number", "Part Number", "sku") or None,
                "description": field_text(line, "description", "Description") or None,
                "quantity": _display(qty)
                if qty is not None
                else field_text(line, "quantity", "qty", "Quantity") or None,
                "uom": field_text(line, "uom", "UOM") or None,
                "rate": _display(rate) if rate is not None else None,
                "line_amount": _display(amt) if amt is not None else None,
            }
        )
    return {
        "invoice_header": header,
        "Line Item": lines_out,
    }


def _matter_validation(matter: dict[str, Any]) -> dict[str, Any]:
    if not matter:
        return {
            "status": "NOT_PRESENT",
            "matter_id": None,
            "client_name": None,
            "matter_master_match": None,
            "needs_manual_entry": True,
            "validation_details": {
                "reason": "Matter ID not found on invoice; route for manual entry."
            },
        }
    status = str(matter.get("status") or "NOT_PRESENT").upper()
    if status in {"SKIPPED", "MISSING"}:
        status = "NOT_PRESENT"
    return {
        "status": status,
        "matter_id": matter.get("matter_id"),
        "client_name": matter.get("client_name"),
        "matter_master_match": matter.get("matter_master_match"),
        "needs_manual_entry": status in {"NOT_PRESENT", "NOT_IN_MASTER"},
        "validation_details": {"reason": matter.get("reason") or ""},
    }


def _display(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.2f}"
    return value
