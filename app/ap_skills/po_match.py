"""po_match — invoice ↔ purchase order (masters via Ezofis client)."""
from __future__ import annotations

import json
from typing import Any

from app.ap_skills.types import (
    ApContext,
    ApSkillResult,
    decision_from_score,
    field_number,
    field_text,
    invoice_from,
    name_similarity,
)

SKILL_ID = "po_match"

# Form labels Core writes from AIAGENTResponse.po_row (fill-only onto ezfb).
# invoice_keys: skip when the invoice already has that field.
_PO_ROW_SCALARS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "PO Amount",
        ("PO Amount", "PO_Amount", "total", "amount"),
        ("PO Amount", "PO_Amount", "po_amount"),
    ),
    (
        "Currency",
        ("currency", "Currency"),
        ("currency", "Currency"),
    ),
    (
        "PO Date",
        ("PO Date", "PO_Date", "po_date", "poDate"),
        ("PO Date", "PO_Date", "po_date"),
    ),
    (
        "Due Date",
        ("Due Date", "Due_Date", "due_date"),
        ("Due Date", "Due_Date", "due_date"),
    ),
    (
        "Terms",
        ("Terms", "TERMS", "terms"),
        ("Terms", "TERMS", "terms"),
    ),
    (
        "Buyer",
        ("Buyer", "Buyer Name", "buyer"),
        ("Buyer", "Buyer Name", "buyer"),
    ),
    (
        "Supplier Id",
        ("Supplier Id", "Supplier ID", "supplier_id", "supplierId"),
        ("Supplier Id", "Supplier ID", "supplier_id", "supplierId"),
    ),
    (
        "Supplier Address",
        ("Supplier Address", "Supplier_Address", "supplier_address", "Vendor Address", "vendor_address"),
        ("Supplier Address", "supplier_address", "Vendor Address", "vendor_address"),
    ),
    (
        "Ship To Address",
        ("Ship To Address", "Ship_To_Address", "ship_to_address", "ship_to"),
        ("Ship To Address", "ship_to_address", "ship_to"),
    ),
    (
        "Vendor",
        ("vendor", "supplier", "Vendor", "Supplier", "Vendor Name", "supplierName"),
        ("vendor", "supplier", "Vendor", "Supplier", "Vendor Name"),
    ),
)


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    po_number = field_text(invoice, "po_number", "poNumber", "po")
    if not po_number:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": None,
                "po": None,
                "score": 0,
                "decision": "NOT_MATCHED",
                "reason": "Invoice has no PO number.",
            },
        )

    po = None
    sap_lookup_reason: str | None = None
    for connector_skill in ("po_lookup_sap", "po_lookup_quickbooks", "po_lookup_sage"):
        artifact = ctx.artifacts.get(connector_skill) or {}
        if not isinstance(artifact, dict) or artifact.get("skipped"):
            continue
        if isinstance(artifact.get("po"), dict):
            po = artifact["po"]
            break
        if connector_skill == "po_lookup_sap" and artifact.get("po_number"):
            # SAP ran and missed / failed — keep its reason if form master also misses.
            sap_lookup_reason = str(artifact.get("reason") or "").strip() or None
    if not po:
        job = ctx.document_job or {}
        from app.ap_skills.hana_po import wants_form_po_master

        # Connector masters (SAP/HANA/QB/Sage): do not fall back to form/ezfb.
        if not wants_form_po_master(job, thresholds=ctx.thresholds):
            reason = sap_lookup_reason or f"PO {po_number} was not found."
            return ApSkillResult(
                skill_id=SKILL_ID,
                data={
                    "po_number": po_number,
                    "po": None,
                    "score": 0,
                    "decision": "NOT_MATCHED",
                    "reason": reason,
                },
            )

        # InternalForm: Workflow PoMaster form (master_form_id); invoice form_id is write-back only.
        form_id = (
            str(job.get("master_form_id") or job.get("masterFormId") or "").strip()
            or ctx.form_id
            or str(job.get("form_id") or "").strip()
            or None
        )
        po = await ctx.ezofis.lookup_po(
            tenant_id=ctx.tenant_id,
            po_number=po_number,
            form_id=form_id,
        )
        # Live Core has no GET /masters/po yet — read ezfb_*_items on the tenant DB.
        if not po and form_id and ctx.store is not None and hasattr(ctx.store, "lookup_ezfb_po"):
            try:
                po = await ctx.store.lookup_ezfb_po(
                    tenant_id=ctx.tenant_id,
                    form_id=form_id,
                    po_number=po_number,
                )
            except Exception:
                po = None
    if not po:
        reason = sap_lookup_reason or f"PO {po_number} was not found."
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "po_number": po_number,
                "po": None,
                "score": 0,
                "decision": "NOT_MATCHED",
                "reason": reason,
            },
        )

    approved = int(ctx.thresholds.get("approved") or ctx.settings.ap_approved_threshold)
    partial = int(ctx.thresholds.get("partial") or ctx.settings.ap_partial_threshold)
    tolerance = float(ctx.thresholds.get("amount_tolerance") or ctx.settings.ap_amount_tolerance)

    score = 40.0
    reasons: list[str] = [f"PO {po_number} found."]
    inv_vendor = field_text(invoice, "vendor", "supplier")
    po_vendor = field_text(po, "vendor", "supplier", "Vendor Name", "Vendor")
    sim = name_similarity(inv_vendor, po_vendor) if inv_vendor and po_vendor else 0.0
    score += round(30.0 * sim, 2)
    if sim >= 0.85:
        reasons.append("Vendor matches PO.")
    elif inv_vendor and po_vendor:
        reasons.append("Vendor differs from PO vendor.")

    inv_total = field_number(invoice, "total", "amount")
    po_total = field_number(po, "total", "amount")
    if inv_total is not None and po_total is not None and po_total != 0:
        delta = abs(inv_total - po_total) / abs(po_total)
        if delta <= tolerance:
            score += 30.0
            reasons.append("Totals match within tolerance.")
        else:
            reasons.append("Totals differ.")
    elif inv_total is None or po_total is None:
        reasons.append("Missing total on invoice or PO.")

    score = min(100.0, round(score, 2))
    data: dict[str, Any] = {
        "po_number": po_number,
        "po": po,
        "score": score,
        "decision": decision_from_score(score, approved=approved, partial=partial),
        "vendor_similarity": sim,
        "reason": " ".join(reasons),
    }
    if isinstance(po, dict) and po.get("form_id"):
        data["form_id"] = po.get("form_id")
    if isinstance(po, dict) and po.get("ezfb_table"):
        data["ezfb_table"] = po.get("ezfb_table")
    po_row = build_po_row(invoice, po if isinstance(po, dict) else {})
    if po_row:
        data["po_row"] = po_row
    return ApSkillResult(
        skill_id=SKILL_ID,
        data=data,
    )


def _invoice_has(invoice: dict[str, Any], *keys: str) -> bool:
    if field_text(invoice, *keys):
        return True
    return field_number(invoice, *keys) is not None


def _po_scalar(po: dict[str, Any], *keys: str) -> Any:
    text = field_text(po, *keys)
    if text:
        return text
    number = field_number(po, *keys)
    if number is None:
        return None
    if float(number).is_integer():
        return int(number)
    return number


def _po_line_mapped(po: dict[str, Any]) -> list[dict[str, Any]]:
    raw: Any = None
    for key in ("PO Line Item Mapped", "PO Line Item", "PO_Line_Item", "match_items", "lines", "items"):
        value = po.get(key)
        if value:
            raw = value
            break
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    mapped: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict) or not row:
            continue
        if any(key in row for key in ("Description", "Quantity", "Unit Cost", "Line", "Material Id", "Item Number")):
            # Already display-shaped (or partially); still fill SAP columns if missing.
            item = dict(row)
        else:
            item = {}
        line_id = (
            row.get("itemNumber")
            or row.get("item_number")
            or row.get("id")
            or row.get("Line")
            or row.get("line")
            or row.get("line_no")
        )
        if line_id not in (None, "") and "Line" not in item:
            item["Line"] = line_id
        if "Item Number" not in item and line_id not in (None, ""):
            item["Item Number"] = line_id

        for label, keys in (
            ("Item Category", ("itemCategory", "item_category", "Item Category")),
            ("Material Id", ("materialId", "material_id", "Material Id", "Part Number", "part_number", "item_no")),
            ("Material Description", ("materialDescription", "material_description", "description", "Description")),
            ("Material Group", ("materialGroup", "material_group", "Material Group")),
            ("Plant", ("plant", "Plant")),
            ("Unit of Measure", ("unitOfMeasure", "unit_of_measure", "UOM", "uom")),
            ("Price Unit", ("priceUnit", "price_unit", "Price Unit")),
        ):
            if item.get(label) not in (None, ""):
                continue
            text = field_text(row, *keys)
            if text:
                item[label] = text

        description = field_text(row, "description", "Description", "materialDescription", "material_description")
        if description and "Description" not in item:
            item["Description"] = description
        if description and "Material Description" not in item:
            item["Material Description"] = description

        qty = field_number(row, "orderQuantity", "order_quantity", "qty", "Quantity", "quantity")
        if qty is not None:
            item.setdefault("Quantity", qty)
            item.setdefault("Order Quantity", qty)
        price = field_number(row, "netPrice", "net_price", "price", "Unit Cost", "unit_cost", "unit_price", "rate")
        if price is not None:
            item.setdefault("Unit Cost", price)
            item.setdefault("Net Price", price)
        amount = field_number(row, "netValue", "net_value", "amount", "Extended", "extended", "line_amount")
        if amount is not None:
            item.setdefault("Extended", amount)
            item.setdefault("Net Value", amount)
        if item:
            mapped.append(item)
    return mapped


def build_po_row(invoice: dict[str, Any], po: dict[str, Any]) -> dict[str, Any]:
    """PO Master fields that the invoice does not already have, keyed for Core po_row."""
    if not isinstance(po, dict) or not po:
        return {}
    # Offline ACME mocks skip form fill; configured SAP sample masters may fill.
    if po.get("mock") and str(po.get("source") or "").strip().lower() != "sap_sample":
        return {}
    row: dict[str, Any] = {}
    for label, po_keys, invoice_keys in _PO_ROW_SCALARS:
        if _invoice_has(invoice, *invoice_keys):
            continue
        value = _po_scalar(po, *po_keys)
        if value in (None, ""):
            continue
        row[label] = value
    if not _invoice_has(invoice, "PO Line Item", "PO_Line_Item", "PO Line Item Mapped"):
        lines = _po_line_mapped(po)
        if lines:
            row["PO Line Item Mapped"] = lines
    return row
