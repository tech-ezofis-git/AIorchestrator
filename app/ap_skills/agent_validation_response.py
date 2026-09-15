"""Build apagentv6-shaped AIAGENTResponse for agent_data_validation."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.ap_skills.agent_validation_text import enrichment_for_finalize, resolve_source_type
from app.ap_skills.payment_terms import due_date_from_terms, normalize_payment_terms
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
    payment_terms = _payment_terms(invoice, po)

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
        "payment_terms": payment_terms,
        "supplier_validation": _supplier_validation(vendor, invoice),
        "invoice_errors": _invoice_errors(duplicate, finalize),
        "back_order": _back_order(backorder, finalize),
        "Extracted Invoice JSON": _extracted_invoice_json(invoice, review, payment_terms),
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
    for key in ("PO Line Item Mapped", "match_items", "lines", "PO Line Item", "PO_Line_Item", "items"):
        raw = po.get(key)
        if isinstance(raw, str) and raw.strip().startswith("["):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = None
        if isinstance(raw, list) and raw:
            return [r for r in raw if isinstance(r, dict)]
    return []


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is None or value == "":
            continue
        return value
    return None


def _display_line_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return _display(value)
    text = str(value).strip()
    return text or None


def _map_po_line_for_display(line: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Map one PO master line to display columns (SAP/HANA + InternalForm)."""
    item: dict[str, Any] = {}

    # Preferred SAP / HANA Cloud PURCHASE_ORDERS item columns.
    sap_fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Item Number", ("itemNumber", "item_number", "Line", "line_no", "line", "id")),
        ("Item Category", ("itemCategory", "item_category", "Item Category")),
        ("Material Id", ("materialId", "material_id", "Material Id", "Part Number", "part_number", "item_no", "sku")),
        (
            "Material Description",
            ("materialDescription", "material_description", "Material Description", "description", "Description"),
        ),
        ("Material Group", ("materialGroup", "material_group", "Material Group")),
        ("Plant", ("plant", "Plant")),
        ("Order Quantity", ("orderQuantity", "order_quantity", "quantity", "qty", "Quantity")),
        ("Unit of Measure", ("unitOfMeasure", "unit_of_measure", "UOM", "uom")),
        ("Net Price", ("netPrice", "net_price", "rate", "price", "Unit Cost", "unit_price")),
        ("Price Unit", ("priceUnit", "price_unit", "Price Unit")),
        ("Net Value", ("netValue", "net_value", "line_amount", "amount", "Extended")),
    )
    for label, keys in sap_fields:
        raw = _first_present(line, *keys)
        if raw is None:
            continue
        if label == "Item Number":
            item[label] = str(raw).strip()
        elif label in {"Order Quantity", "Net Price", "Price Unit", "Net Value"}:
            num = field_number(line, *keys)
            if num is not None:
                item[label] = int(num) if float(num).is_integer() else num
            else:
                item[label] = _display_line_value(raw)
        else:
            item[label] = _display_line_value(raw)

    if "Item Number" not in item:
        item["Item Number"] = str(index)

    # Backward-compatible aliases used by existing UI / tests.
    aliases = (
        ("Line", "Item Number"),
        ("Part Number", "Material Id"),
        ("description", "Material Description"),
        ("UOM", "Unit of Measure"),
        ("quantity", "Order Quantity"),
        ("rate", "Net Price"),
        ("line_amount", "Net Value"),
    )
    for alias, src in aliases:
        if alias not in item and src in item:
            item[alias] = item[src]

    # Extra InternalForm / SAP columns not covered above.
    for label, keys in (
        ("Tax", ("Tax", "tax")),
        ("Req Date", ("Req Date", "req_date", "delivery_date")),
        ("Weight", ("Weight", "weight")),
        ("G/L Account", ("G/L Account", "gl_account", "gl")),
    ):
        if label in item:
            continue
        raw = _first_present(line, *keys)
        if raw is not None:
            item[label] = _display_line_value(raw)

    # Pass through any remaining SAP master keys (camelCase) not already mapped.
    known = {
        "itemNumber",
        "item_number",
        "itemCategory",
        "item_category",
        "materialId",
        "material_id",
        "materialDescription",
        "material_description",
        "materialGroup",
        "material_group",
        "plant",
        "orderQuantity",
        "order_quantity",
        "unitOfMeasure",
        "unit_of_measure",
        "netPrice",
        "net_price",
        "priceUnit",
        "price_unit",
        "netValue",
        "net_value",
        "line_no",
        "line",
        "id",
        "description",
        "Description",
        "qty",
        "quantity",
        "Quantity",
        "unit_price",
        "price",
        "rate",
        "amount",
        "line_amount",
        "uom",
        "UOM",
        "part_number",
        "Part Number",
        "item_no",
        "sku",
        "Tax",
        "tax",
        "Req Date",
        "req_date",
        "delivery_date",
        "Weight",
        "weight",
        "G/L Account",
        "gl_account",
        "gl",
        "Line",
    }
    for key, value in line.items():
        if key in known or key in item or value is None or value == "":
            continue
        if str(key).startswith("_"):
            continue
        item[key] = value

    return {k: v for k, v in item.items() if v is not None and v != ""}


def _build_display_po_row(po: dict[str, Any], fill_row: dict[str, Any]) -> dict[str, Any]:
    if not po and not fill_row:
        return {}
    row: dict[str, Any] = {}
    if fill_row:
        row.update(fill_row)
    if not po:
        return row

    mapping = (
        ("PO Number", ("po_number", "PO Number", "poNumber"), "text"),
        ("Supplier", ("vendor", "supplier", "Supplier", "Vendor Name", "Vendor", "supplierName"), "text"),
        ("Supplier Id", ("supplier_id", "supplierId", "Supplier Id", "Supplier ID"), "text"),
        ("Supplier Address", ("supplier_address", "Supplier Address", "vendor_address", "Vendor Address"), "text"),
        ("Ship To Address", ("ship_to_address", "Ship To Address", "ship_to"), "text"),
        ("PO Date", ("po_date", "PO Date", "PO DATE", "poDate"), "text"),
        ("Terms", ("terms", "Terms", "TERMS"), "text"),
        ("Due Date", ("due_date", "Due Date"), "text"),
        ("Buyer", ("buyer", "Buyer"), "text"),
        ("PO Amount", ("total", "amount", "PO Amount"), "number"),
        ("Currency", ("currency", "Currency"), "text"),
    )
    for label, keys, kind in mapping:
        if row.get(label) not in (None, ""):
            continue
        if kind == "number":
            num = field_number(po, *keys)
            if num is not None:
                row[label] = int(num) if float(num).is_integer() else num
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
            cleaned = [_map_po_line_for_display(line, index=idx) for idx, line in enumerate(lines, start=1)]
            cleaned = [item for item in cleaned if item]
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
        "matches",
    ):
        if meta in po and meta not in row:
            row[meta] = po[meta]

    # Pass through remaining SAP/HANA header columns not already mapped.
    header_skip = {
        "po_number",
        "PO Number",
        "poNumber",
        "vendor",
        "supplier",
        "Supplier",
        "Vendor Name",
        "Vendor",
        "supplierName",
        "supplier_id",
        "supplierId",
        "lines",
        "items",
        "match_items",
        "PO Line Item Mapped",
        "PO Line Item",
        "PO_Line_Item",
        "lookup_error",
        "reason",
        "mock",
        "form_id",
        "ezfb_table",
    }
    for key, value in po.items():
        if key in header_skip or key in row or value is None or value == "":
            continue
        if isinstance(value, (list, dict)) and key not in ("matches",):
            continue
        row[key] = value
    return row


def _payment_terms(invoice: dict[str, Any], po: dict[str, Any]) -> dict[str, Any]:
    raw = field_text(invoice, "terms", "Terms", "TERMS", "Payment Terms", "payment_terms") or field_text(
        po, "terms", "Terms", "TERMS", "Payment Terms", "payment_terms"
    )
    invoice_date = field_text(invoice, "invoice_date", "Invoice Date", "Document Date")
    due_date = field_text(invoice, "due_date", "Due Date")
    normalized = normalize_payment_terms(raw)
    computed = due_date_from_terms(invoice_date=invoice_date, terms=raw)
    if computed:
        due_date = computed
    elif not due_date:
        due_date = field_text(po, "due_date", "Due Date") or None
    return {
        "raw": raw or None,
        "normalized": normalized,
        "invoice_date": invoice_date or None,
        "due_date": due_date or None,
    }


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
        "status": status if status not in (None, "", "MISSING", "UNVERIFIED") else None,
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


def _extracted_invoice_json(
    invoice: dict[str, Any],
    review: str,
    payment_terms: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    header_src = invoice.get("invoice_header") if isinstance(invoice.get("invoice_header"), dict) else invoice
    inv_amt = field_number(header_src, "total", "amount", "Invoice Amount")
    tax_amt = field_number(header_src, "tax", "tax_amount", "Invoice Tax Amount")
    terms = (
        field_text(header_src, "terms", "Terms", "TERMS", "Payment Terms")
        or (payment_terms or {}).get("raw")
        or None
    )
    due_date = (
        (payment_terms or {}).get("due_date")
        or field_text(header_src, "due_date", "Due Date")
        or None
    )
    invoice_date = (
        field_text(header_src, "invoice_date", "Invoice Date", "Document Date")
        or (payment_terms or {}).get("invoice_date")
        or None
    )
    header = {
        "Document Type": field_text(header_src, "doc_type", "Document Type", "document_type") or "INVOICE",
        "Invoice No": field_text(header_src, "invoice_number", "Invoice No", "invoice_no") or None,
        "PO Number": field_text(header_src, "po_number", "PO Number", "poNumber") or None,
        "PO DATE": field_text(header_src, "po_date", "PO Date", "PO DATE") or None,
        "Vendor Name": field_text(header_src, "vendor", "Vendor Name", "supplier", "Supplier") or None,
        "Currency": field_text(header_src, "currency", "Currency") or None,
        "TERMS": terms,
        "PO Amount": field_text(header_src, "po_amount", "PO Amount") or None,
        "Invoice Date": invoice_date,
        "Due Date": due_date,
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
