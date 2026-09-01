"""Coordinate-based Template Renderer for PDF Generator.

Renders pdfme-style template definitions (schemas, basePdf, coordinates,
variables, dynamic tables) into publication-quality PDFs via ReportLab.
"""
from __future__ import annotations

import ast
import base64
import html
import io
import json
import logging
import math
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import fitz  # PyMuPDF
from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

logger = logging.getLogger("orchestrator.pdf_skills.template_renderer")

STANDARD_FONTS = {
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
}


def is_pdfme_template(data: Any) -> bool:
    """Checks if data matches the pdfme template structure."""
    if not isinstance(data, dict):
        return False
    return "schemas" in data and isinstance(data["schemas"], list)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def is_font_available(font_name: str) -> bool:
    if not font_name:
        return False
    if font_name in STANDARD_FONTS:
        return True
    try:
        pdfmetrics.getFont(font_name)
        return True
    except KeyError:
        return False


def resolve_font_name(requested: Optional[str]) -> str:
    if requested and is_font_available(requested):
        return requested

    if requested:
        lower = requested.lower()
        if "bold" in lower and is_font_available("Helvetica-Bold"):
            return "Helvetica-Bold"
        if ("italic" in lower or "oblique" in lower) and is_font_available("Helvetica-Oblique"):
            return "Helvetica-Oblique"

    return "Helvetica"


def hex_to_color(value: Any, default: Optional[Color] = None) -> Optional[Color]:
    if value is None:
        return default
    if isinstance(value, Color):
        return value
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return default
        if val.lower() in colors.getAllNamedColors():
            return colors.getAllNamedColors()[val.lower()]
        if val.startswith("#"):
            val = val[1:]
        if len(val) == 6:
            try:
                r = int(val[0:2], 16) / 255.0
                g = int(val[2:4], 16) / 255.0
                b = int(val[4:6], 16) / 255.0
                return Color(r, g, b)
            except ValueError:
                return default
        if len(val) == 8:
            try:
                r = int(val[0:2], 16) / 255.0
                g = int(val[2:4], 16) / 255.0
                b = int(val[4:6], 16) / 255.0
                a = int(val[6:8], 16) / 255.0
                return Color(r, g, b, alpha=a)
            except ValueError:
                return default
    return default


def apply_opacity(c: canvas.Canvas, opacity: Optional[float]) -> None:
    op = safe_float(opacity, 1.0)
    op = max(0.0, min(1.0, op))
    try:
        c.setFillAlpha(op)
        c.setStrokeAlpha(op)
    except AttributeError:
        pass


def detect_page_unit(page_width: float, page_height: float, override: str = "auto") -> str:
    if override in {"mm", "pt"}:
        return override
    common_mm_sizes = [
        (210, 297),   # A4
        (148, 210),   # A5
        (297, 420),   # A3
        (216, 279),   # Letter
        (216, 356),   # Legal
    ]
    for w, h in common_mm_sizes:
        if abs(page_width - w) <= 3 and abs(page_height - h) <= 3:
            return "mm"
    if page_width <= 400 and page_height <= 400:
        return "mm"
    return "pt"


def convert_page_measure(value: Any, unit: str) -> float:
    numeric = safe_float(value, 0.0)
    if unit == "mm":
        return numeric * mm
    return numeric


def parse_page_padding(padding: Any, unit: str) -> Dict[str, float]:
    if padding is None:
        return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
    if isinstance(padding, (int, float)):
        v = convert_page_measure(padding, unit)
        return {"top": v, "right": v, "bottom": v, "left": v}
    if isinstance(padding, (list, tuple)) and len(padding) == 4:
        return {
            "top": convert_page_measure(padding[0], unit),
            "right": convert_page_measure(padding[1], unit),
            "bottom": convert_page_measure(padding[2], unit),
            "left": convert_page_measure(padding[3], unit),
        }
    if isinstance(padding, dict):
        return {
            "top": convert_page_measure(padding.get("top", 0), unit),
            "right": convert_page_measure(padding.get("right", 0), unit),
            "bottom": convert_page_measure(padding.get("bottom", 0), unit),
            "left": convert_page_measure(padding.get("left", 0), unit),
        }
    return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}


def parse_inner_padding_points(padding: Any) -> Dict[str, float]:
    if padding is None:
        return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
    if isinstance(padding, (int, float)):
        v = safe_float(padding)
        return {"top": v, "right": v, "bottom": v, "left": v}
    if isinstance(padding, (list, tuple)) and len(padding) == 4:
        return {
            "top": safe_float(padding[0]),
            "right": safe_float(padding[1]),
            "bottom": safe_float(padding[2]),
            "left": safe_float(padding[3]),
        }
    if isinstance(padding, dict):
        return {
            "top": safe_float(padding.get("top", 0)),
            "right": safe_float(padding.get("right", 0)),
            "bottom": safe_float(padding.get("bottom", 0)),
            "left": safe_float(padding.get("left", 0)),
        }
    return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}


def parse_border_width_points(border_width: Any, default: float = 0.0) -> Dict[str, float]:
    if border_width is None:
        return {"top": default, "right": default, "bottom": default, "left": default}
    if isinstance(border_width, (int, float)):
        v = safe_float(border_width, default)
        return {"top": v, "right": v, "bottom": v, "left": v}
    if isinstance(border_width, dict):
        return {
            "top": safe_float(border_width.get("top", default), default),
            "right": safe_float(border_width.get("right", default), default),
            "bottom": safe_float(border_width.get("bottom", default), default),
            "left": safe_float(border_width.get("left", default), default),
        }
    return {"top": default, "right": default, "bottom": default, "left": default}


def alignment_to_ta(value: str) -> int:
    v = (value or "left").lower()
    if v == "center":
        return TA_CENTER
    if v == "right":
        return TA_RIGHT
    if v == "justify":
        return TA_JUSTIFY
    return TA_LEFT


def table_valign(value: str) -> str:
    v = (value or "middle").lower()
    if v in {"top", "start"}:
        return "TOP"
    if v in {"bottom", "end"}:
        return "BOTTOM"
    return "MIDDLE"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    t = str(value)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00A0", " ")
    return t


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return default
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(s)
            except (SyntaxError, ValueError):
                return default
    return default


def substitute_variables(text: str, variables: Sequence[Any], data: Dict[str, Any]) -> str:
    result = normalize_text(text)
    variable_map: Dict[str, Any] = {}

    for item in variables or []:
        if isinstance(item, dict):
            k = item.get("name") or item.get("key")
            if k:
                variable_map[k] = data.get(k, item.get("value", ""))

    for k, v in data.items():
        if isinstance(k, str):
            variable_map[k] = v

    for k, v in variable_map.items():
        s_val = "" if v is None else str(v)
        result = result.replace("{{" + k + "}}", s_val)
        result = result.replace("${" + k + "}", s_val)
        result = result.replace("{" + k + "}", s_val)

    return result


def collapse_exact_duplicated_text(text: str) -> str:
    norm = normalize_text(text)
    length = len(norm)
    # Never collapse numbers, short words, or strings without spaces
    if length < 25 or norm.strip().isdigit() or " " not in norm:
        return norm

    first_repeat = norm.find(norm[:80], 1) if length >= 160 else -1
    if first_repeat > 0:
        prefix = norm[:first_repeat]
        suffix = norm[first_repeat:]
        if prefix == suffix:
            return prefix

    midpoint = length // 2
    for delta in (-1, 0, 1):
        split_at = midpoint + delta
        if split_at <= 0 or split_at >= length:
            continue
        if norm[:split_at] == norm[split_at:]:
            return norm[:split_at]

    return norm


def get_field_lookup_keys(field: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    for candidate in (
        field.get("dataKey"),
        field.get("sourceFieldId"),
        field.get("name"),
        field.get("placeholder"),
    ):
        if isinstance(candidate, str):
            s = candidate.strip()
            if s and s not in keys:
                keys.append(s)
    return keys


def normalize_key(k: Any) -> str:
    if k is None:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(k).lower())


def auto_flatten_and_enrich_data(data: Any) -> Dict[str, Any]:
    """Expands list of items into row keys and enriches common semantic aliases."""
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        elif len(data) > 0 and isinstance(data[0], dict) and any("Customer" in k or "Vessel" in k or "Document" in k or "No." in k or "Actual Cost" in k for k in data[0].keys()):
            data = data[0]
        else:
            data = {"records": data}
    elif not isinstance(data, dict):
        return {}

    enriched: Dict[str, Any] = dict(data)

    # 1. Flatten array items into row keys
    array_keys = [
        "items",
        "disbursement_items",
        "estimated_costs",
        "costs",
        "services",
        "records",
        "cost_items",
        "port_call_costs",
        "cost_details",
        "Cost Details",
    ]
    items_list = None
    for ak in array_keys:
        val = data.get(ak)
        if isinstance(val, list) and val:
            items_list = val
            break

    if items_list:
        # Also store under Cost Details for table-based templates
        if "Cost Details" not in enriched:
            enriched["Cost Details"] = items_list
        if "cost_details" not in enriched:
            enriched["cost_details"] = items_list

        for idx, item in enumerate(items_list, start=1):
            if isinstance(item, dict):
                # Number
                no_val = item.get("no") or item.get("code") or idx
                enriched[f"No. {idx}"] = no_val
                enriched[f"No {idx}"] = no_val
                enriched[f"no_{idx}"] = no_val

                # Service / Cost Item
                srv = (
                    item.get("service")
                    or item.get("service_cost_item")
                    or item.get("cost_head")
                    or item.get("item")
                    or item.get("description")
                    or item.get("name")
                    or ""
                )
                if srv:
                    enriched[f"Service / Cost Item {idx}"] = srv
                    enriched[f"ServiceCostItem{idx}"] = srv
                    enriched[f"service_{idx}"] = srv

                # Vendor / Basis
                vnd = (
                    item.get("vendor")
                    or item.get("basis")
                    or item.get("vendor_basis")
                    or item.get("tariff")
                    or item.get("notes")
                    or ""
                )
                if vnd != "":
                    enriched[f"Vendor / Basis {idx}"] = vnd
                    enriched[f"VendorBasis{idx}"] = vnd
                    enriched[f"vendor_{idx}"] = vnd

                # Qty
                qty = (
                    item.get("qty")
                    or item.get("quantity")
                    or item.get("count")
                    or (1 if item.get("rate") is not None else "")
                )
                if qty != "":
                    enriched[f"Qty {idx}"] = str(qty)
                    enriched[f"Qty{idx}"] = str(qty)
                    enriched[f"qty_{idx}"] = str(qty)

                # Rate
                rate = (
                    item.get("rate")
                    or item.get("unit_price")
                    or item.get("unit_cost")
                    or item.get("tariff")
                    or ""
                )
                if rate != "":
                    rate_str = f"{float(rate):,.2f}" if isinstance(rate, (int, float)) else str(rate)
                    enriched[f"Rate {idx}"] = rate_str
                    enriched[f"Rate{idx}"] = rate_str
                    enriched[f"rate_{idx}"] = rate_str

                # Amount
                amt = (
                    item.get("amount")
                    or item.get("total")
                    or item.get("actual")
                    or item.get("cost")
                    or ""
                )
                if amt != "":
                    amt_str = f"{float(amt):,.2f}" if isinstance(amt, (int, float)) else str(amt)
                    enriched[f"Amount (SGD) {idx}"] = amt_str
                    enriched[f"Amount (EUR) {idx}"] = amt_str
                    enriched[f"Amount (USD) {idx}"] = amt_str
                    enriched[f"Amount {idx}"] = amt_str
                    enriched[f"AmountSGD{idx}"] = amt_str
                    enriched[f"amount_{idx}"] = amt_str

    # 2. Map semantic aliases for Vessel Call forms
    if "arrival_date" in data and "departure_date" in data and "ETA / ETD" not in enriched:
        enriched["ETA / ETD"] = f"{data['arrival_date']} / {data['departure_date']}"
    elif "eta" in data and "etd" in data and "ETA / ETD" not in enriched:
        enriched["ETA / ETD"] = f"{data['eta']} / {data['etd']}"

    if "total_disbursement" in data:
        val = data["total_disbursement"]
        val_str = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
        if "Actual Cost + Tax" not in enriched:
            enriched["Actual Cost + Tax"] = val_str
        if "TOTAL PORT CALL COSTS" not in enriched:
            enriched["TOTAL PORT CALL COSTS"] = val_str
    elif "total_port_call_costs" in data:
        val = data["total_port_call_costs"]
        val_str = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
        if "Actual Cost + Tax" not in enriched:
            enriched["Actual Cost + Tax"] = val_str
        if "TOTAL PORT CALL COSTS" not in enriched:
            enriched["TOTAL PORT CALL COSTS"] = val_str

    if "advance_received" in data:
        val = data["advance_received"]
        val_str = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
        if "Less PDA Advance" not in enriched:
            enriched["Less PDA Advance"] = val_str
        if "LESS: ADVANCE RECEIVED (PDA)" not in enriched:
            enriched["LESS: ADVANCE RECEIVED (PDA)"] = val_str
    elif "advance" in data:
        val = data["advance"]
        val_str = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
        if "Less PDA Advance" not in enriched:
            enriched["Less PDA Advance"] = val_str
        if "LESS: ADVANCE RECEIVED (PDA)" not in enriched:
            enriched["LESS: ADVANCE RECEIVED (PDA)"] = val_str

    if "balance_due_to_principal" in data and "BALANCE DUE" not in enriched:
        val = data["balance_due_to_principal"]
        val_str = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
        enriched["BALANCE DUE"] = f"({val_str}) Due to Principal"
    elif "balance_due" in data and "BALANCE DUE" not in enriched:
        val = data["balance_due"]
        enriched["BALANCE DUE"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)

    if "principal" in data and "Customer / Principal" not in enriched:
        enriched["Customer / Principal"] = data["principal"]

    if "agent_name" in data and "Shipper / Charterer / Broker" not in enriched:
        enriched["Shipper / Charterer / Broker"] = data["agent_name"]

    if "call_number" in data and "Document No." not in enriched:
        enriched["Document No."] = data["call_number"]
    elif "proforma_ref" in data and "Document No." not in enriched:
        enriched["Document No."] = data["proforma_ref"]

    if "port_name" in data and "Port of Call" not in enriched:
        enriched["Port of Call"] = data["port_name"]

    if "berth" in data and "Terminal" not in enriched:
        enriched["Terminal"] = data["berth"]

    # PDA specific alias mappings
    if "Job No" not in enriched:
        for alt in ["job_no", "jobno", "call_number", "document_no", "Document No.", "proforma_ref", "doc_no", "ref"]:
            if alt in data and data[alt]:
                enriched["Job No"] = str(data[alt])
                break

    if "Vessel" not in enriched:
        for alt in ["vessel", "vessel_name", "Vessel Name", "ship_name"]:
            if alt in data and data[alt]:
                enriched["Vessel"] = str(data[alt])
                break

    if "Port" not in enriched:
        for alt in ["port", "port_name", "port_of_call", "Port of Call", "portofcall"]:
            if alt in data and data[alt]:
                enriched["Port"] = str(data[alt])
                break

    if "Customer" not in enriched:
        for alt in ["customer", "customer_name", "principal", "Customer / Principal", "client"]:
            if alt in data and data[alt]:
                enriched["Customer"] = str(data[alt])
                break

    if "Shipper" not in enriched:
        for alt in ["shipper", "shipper_name", "charterer", "broker", "Shipper / Charterer / Broker", "agent_name", "agent"]:
            if alt in data and data[alt]:
                enriched["Shipper"] = str(data[alt])
                break

    if "IMO Number" not in enriched:
        for alt in ["imo_number", "imo", "imonumber", "IMO"]:
            if alt in data and data[alt]:
                enriched["IMO Number"] = str(data[alt])
                break

    if "Payment Terms" not in enriched:
        for alt in ["payment_terms", "paymentterms", "terms"]:
            if alt in data and data[alt]:
                enriched["Payment Terms"] = str(data[alt])
                break

    if "Voyage Number" not in enriched:
        for alt in ["voyage_number", "voyage_no", "voyageno", "voyage"]:
            if alt in data and data[alt]:
                enriched["Voyage Number"] = str(data[alt])
                break

    if "Cost Verified" not in enriched:
        for alt in ["cost_verified", "costverified", "verified"]:
            if alt in data and data[alt]:
                enriched["Cost Verified"] = str(data[alt])
                break

    if "Related PDA/FDA" not in enriched:
        for alt in ["related_pda_fda", "related_pda", "related_fda", "related_doc"]:
            if alt in data and data[alt]:
                enriched["Related PDA/FDA"] = str(data[alt])
                break

    if "Terminal" not in enriched:
        for alt in ["terminal", "berth", "terminal_berth"]:
            if alt in data and data[alt]:
                enriched["Terminal"] = str(data[alt])
                break

    if "ETA" not in enriched:
        for alt in ["eta", "arrival_date", "estimated_arrival"]:
            if alt in data and data[alt]:
                enriched["ETA"] = str(data[alt])
                break

    if "ETD" not in enriched:
        for alt in ["etd", "departure_date", "estimated_departure"]:
            if alt in data and data[alt]:
                enriched["ETD"] = str(data[alt])
                break

    if "Agency Appointment Date" not in enriched:
        for alt in ["agency_appointment_date", "appointment_date", "agency_appointment", "document_date", "Document Date", "date"]:
            if alt in data and data[alt]:
                enriched["Agency Appointment Date"] = str(data[alt])
                break

    if "Cargo Type" not in enriched:
        for alt in ["cargo_type", "cargo", "commodity"]:
            if alt in data and data[alt]:
                enriched["Cargo Type"] = str(data[alt])
                break

    if "Cargo Quantity" not in enriched:
        for alt in ["cargo_quantity", "cargo_qty", "quantity"]:
            if alt in data and data[alt]:
                enriched["Cargo Quantity"] = str(data[alt])
                break

    if "Cargo Description" not in enriched:
        for alt in ["cargo_description", "cargo_desc", "description"]:
            if alt in data and data[alt]:
                enriched["Cargo Description"] = str(data[alt])
                break

    # PDA Summary
    if "estimated_subtotal" in data and "Estimated Subtotal" not in enriched:
        val = data["estimated_subtotal"]
        enriched["Estimated Subtotal"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
    elif "subtotal" in data and "Estimated Subtotal" not in enriched:
        val = data["subtotal"]
        enriched["Estimated Subtotal"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
    elif "Subtotal" in data and "Estimated Subtotal" not in enriched:
        val = data["Subtotal"]
        enriched["Estimated Subtotal"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)

    if "estimated_tax" in data and "Estimated Tax (7.45%)" not in enriched:
        val = data["estimated_tax"]
        enriched["Estimated Tax (7.45%)"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
    elif "tax_amount" in data and "Estimated Tax (7.45%)" not in enriched:
        val = data["tax_amount"]
        enriched["Estimated Tax (7.45%)"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)

    if "total_pda" in data and "TOTAL PDA" not in enriched:
        val = data["total_pda"]
        enriched["TOTAL PDA"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
    elif "estimated_total" in data and "TOTAL PDA" not in enriched:
        val = data["estimated_total"]
        enriched["TOTAL PDA"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)
    elif "required_prefunding" in data and "TOTAL PDA" not in enriched:
        val = data["required_prefunding"]
        enriched["TOTAL PDA"] = f"{float(val):,.2f}" if isinstance(val, (int, float)) else str(val)

    # Auto-calculate summary totals from items if missing
    if ("Estimated Subtotal" not in enriched or "TOTAL PDA" not in enriched) and items_list:
        subtotal_sum = 0.0
        tax_sum = 0.0
        for it in items_list:
            if isinstance(it, dict):
                raw_amt = it.get("amount") or it.get("total") or it.get("cost")
                if raw_amt is None and it.get("rate") is not None:
                    try:
                        r_f = float(str(it.get("rate")).replace(",", "").strip())
                        q_f = float(str(it.get("qty") or it.get("quantity") or 1).replace(",", "").strip())
                        raw_amt = r_f * q_f
                    except (ValueError, TypeError):
                        raw_amt = 0
                raw_amt = str(raw_amt or 0).replace(",", "").strip()
                try:
                    amt_f = float(raw_amt)
                    subtotal_sum += amt_f
                    tax_val = it.get("tax_amount") or it.get("tax_amt")
                    if tax_val is not None:
                        tax_sum += float(str(tax_val).replace(",", "").strip())
                    else:
                        tax_pct = float(it.get("tax_percent") or it.get("tax_pct") or it.get("tax") or 7.45)
                        tax_sum += amt_f * (tax_pct / 100.0)
                except (ValueError, TypeError):
                    pass
            elif isinstance(it, list) and len(it) >= 6:
                # 2D list item: e.g. ["PSA", "PSA", "Dues", "1", "4500.00", "4500.00", "7.45", "335.25", "4835.25"]
                try:
                    amt_f = float(str(it[5]).replace(",", "").strip())
                    subtotal_sum += amt_f
                    if len(it) >= 8 and it[7]:
                        tax_sum += float(str(it[7]).replace(",", "").strip())
                    else:
                        tax_sum += amt_f * 0.0745
                except (ValueError, TypeError, IndexError):
                    pass
        if subtotal_sum > 0:
            if "Estimated Subtotal" not in enriched:
                enriched["Estimated Subtotal"] = f"{subtotal_sum:,.2f}"
            if "Estimated Tax (7.45%)" not in enriched:
                if tax_sum == 0.0:
                    tax_sum = subtotal_sum * 0.0745
                enriched["Estimated Tax (7.45%)"] = f"{tax_sum:,.2f}"
            if "TOTAL PDA" not in enriched:
                total_pda_val = subtotal_sum + tax_sum
                enriched["TOTAL PDA"] = f"{total_pda_val:,.2f}"

    if "remarks" in data and "Remarks" not in enriched:
        enriched["Remarks"] = data["remarks"]
    elif "notes" in data and "Remarks" not in enriched:
        enriched["Remarks"] = data["notes"]

    return enriched


def is_static_label_field(field: Dict[str, Any]) -> bool:
    """Returns True if the field is a static label box that should retain its predefined text."""
    if field.get("readOnly") is True:
        return True
    content = str(field.get("content", "")).strip()
    data_key = field.get("dataKey")
    name = str(field.get("name", "")).strip()

    # If it has an explicit dataKey, it is a dynamic value field
    if data_key:
        return False

    # If name explicitly ends with Label or Header or Note, it's static
    if name.endswith("Label") or name.endswith("Header") or name.endswith("Title") or name.endswith("Note"):
        return True

    # If it has non-empty static content and no dataKey and doesn't end with Value
    if content and content not in ("{}", "[]") and not name.endswith("Value"):
        return True

    return False


def resolve_metadata_value(field: Dict[str, Any], data: Dict[str, Any], default: Any = "") -> Any:
    # 1. Exact lookup
    field_keys = get_field_lookup_keys(field)
    for k in field_keys:
        if k in data and data[k] not in (None, ""):
            return data[k]

    # 2. Normalized alphanumeric lookup
    norm_data: Dict[str, Any] = {}
    for dk, dv in data.items():
        if isinstance(dk, str) and dv not in (None, ""):
            norm_data[normalize_key(dk)] = dv

    for k in field_keys:
        nk = normalize_key(k)
        if nk in norm_data:
            return norm_data[nk]
        # Also try stripping trailing 'value'
        nk_strip = re.sub(r"value$", "", nk)
        if nk_strip in norm_data:
            return norm_data[nk_strip]

    # 3. Common semantic aliases for value fields
    field_name = normalize_key(field.get("name") or field.get("dataKey") or "")
    if "jobno" in field_name or "documentno" in field_name or "docno" in field_name or "callnumber" in field_name:
        for alt in ["job_no", "jobno", "document_no", "call_number", "doc_no", "invoice_no", "invoice_number", "proforma_ref", "ref"]:
            if alt in data:
                return data[alt]
    if "vessel" in field_name:
        for alt in ["vessel", "vessel_name", "vesselname", "ship_name"]:
            if alt in data:
                return data[alt]
    if "port" in field_name or "portofcall" in field_name:
        for alt in ["port", "port_name", "port_of_call", "portofcall", "portname"]:
            if alt in data:
                return data[alt]
    if "customer" in field_name or "principal" in field_name:
        for alt in ["customer", "principal", "customer_name", "client"]:
            if alt in data:
                return data[alt]
    if "shipper" in field_name or "broker" in field_name or "charterer" in field_name:
        for alt in ["shipper", "shipper_name", "charterer", "broker", "agent_name", "agent"]:
            if alt in data:
                return data[alt]
    if "imonumber" in field_name or "imo" in field_name:
        for alt in ["imo_number", "imo", "imonumber"]:
            if alt in data:
                return data[alt]
    if "paymentterms" in field_name or "terms" in field_name:
        for alt in ["payment_terms", "paymentterms", "terms"]:
            if alt in data:
                return data[alt]
    if "voyagenumber" in field_name or "voyage" in field_name:
        for alt in ["voyage_number", "voyage_no", "voyageno", "voyage"]:
            if alt in data:
                return data[alt]
    if "costverified" in field_name:
        for alt in ["cost_verified", "costverified", "verified"]:
            if alt in data:
                return data[alt]
    if "terminal" in field_name or "berth" in field_name:
        for alt in ["terminal", "berth", "terminal_berth"]:
            if alt in data:
                return data[alt]
    if field_name in ["eta", "etavalue"]:
        for alt in ["eta", "arrival_date", "estimated_arrival"]:
            if alt in data:
                return data[alt]
    if field_name in ["etd", "etdvalue"]:
        for alt in ["etd", "departure_date", "estimated_departure"]:
            if alt in data:
                return data[alt]
    if "appointment" in field_name:
        for alt in ["agency_appointment_date", "appointment_date", "agency_appointment", "document_date", "date"]:
            if alt in data:
                return data[alt]
    if "cargotype" in field_name:
        for alt in ["cargo_type", "cargo", "commodity"]:
            if alt in data:
                return data[alt]
    if "cargoquantity" in field_name:
        for alt in ["cargo_quantity", "quantity", "cargo_qty", "qty"]:
            if alt in data:
                return data[alt]
    if "cargodescription" in field_name:
        for alt in ["cargo_description", "cargo_desc", "description"]:
            if alt in data:
                return data[alt]
    if "costdetails" in field_name or "details" in field_name or "costs" in field_name:
        for alt in ["Cost Details", "cost_details", "costs", "items", "disbursement_items", "cost_items", "port_call_costs"]:
            if alt in data:
                return data[alt]
    if "subtotal" in field_name or "estimatedsubtotal" in field_name:
        for alt in ["estimated_subtotal", "subtotal", "sub_total"]:
            if alt in data:
                return data[alt]
    if "tax" in field_name or "estimatedtax" in field_name:
        for alt in ["estimated_tax", "tax_amount", "tax", "vat"]:
            if alt in data:
                return data[alt]
    if "totalpda" in field_name or "total" in field_name:
        for alt in ["total_pda", "total", "total_disbursement", "total_amount", "estimated_total", "grand_total"]:
            if alt in data:
                return data[alt]
    if "advance" in field_name or "advancereceived" in field_name:
        for alt in ["advance_received", "advance", "deposit", "prefunding"]:
            if alt in data:
                return data[alt]
    if "balance" in field_name or "balancedue" in field_name:
        for alt in ["balance_due", "balance", "balance_due_to_principal", "net_due"]:
            if alt in data:
                return data[alt]
    if "remarks" in field_name or "notes" in field_name:
        for alt in ["remarks", "notes", "comments", "remark"]:
            if alt in data:
                return data[alt]

    return default


def resolve_text_content(field: Dict[str, Any], data: Dict[str, Any]) -> str:
    field_type = field.get("type")

    # Smart Text with parts
    if field_type == "smartText":
        parts = field.get("parts")
        if not parts:
            parts = safe_json_loads(field.get("partsJson"), default=[])
        if isinstance(parts, list) and parts:
            segments: List[str] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("kind", "text")).strip().lower()
                if kind == "field":
                    candidates = [part.get("dataKey"), part.get("sourceFieldId"), part.get("name")]
                    val = None
                    for c in candidates:
                        if isinstance(c, str) and c.strip() in data:
                            val = data[c.strip()]
                            break
                    if val is None:
                        val = part.get("value", part.get("placeholder", ""))
                    segments.append(normalize_text(val))
                else:
                    segments.append(normalize_text(part.get("value", "")))
            combined = "".join(segments)
            combined = substitute_variables(combined, field.get("variables", []), data)
            return collapse_exact_duplicated_text(combined)

    # 1. Static Label Box
    if is_static_label_field(field):
        raw_content = field.get("content", field.get("text", ""))
        return collapse_exact_duplicated_text(normalize_text(raw_content))

    # 2. Dynamic Value Box
    base = resolve_metadata_value(field, data, default="")
    if base == "" or base is None:
        fc = field.get("content", field.get("text", ""))
        if fc not in ("{}", "[]", None, ""):
            base = fc
        else:
            base = ""

    resolved = substitute_variables(normalize_text(base), field.get("variables", []), data)
    return collapse_exact_duplicated_text(resolved)


def _format_table_val(val: Any) -> str:
    """Helper to format table cell values without returning '-' for empty."""
    if val is None or val == "":
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)):
        if isinstance(val, float):
            return f"{val:,.2f}"
        return f"{val:,}"
    s = str(val).strip()
    if s == "-" or s == "":
        return ""
    return s


def resolve_table_rows(field: Dict[str, Any], data: Dict[str, Any]) -> List[List[Any]]:
    # 1. Try to find raw table data using field lookups
    raw = resolve_metadata_value(field, data, None)

    # 2. If None, check common array keys
    if raw is None or raw == "":
        for candidate_key in [
            "Cost Details",
            "cost_details",
            "CostDetails",
            "items",
            "disbursement_items",
            "costs",
            "cost_items",
            "port_call_costs",
            "services",
            "records",
        ]:
            if candidate_key in data and data[candidate_key]:
                raw = data[candidate_key]
                break

    if raw is None or raw == "":
        raw = field.get("content", [])

    parsed = safe_json_loads(raw, default=[])
    if not isinstance(parsed, list):
        return []

    headers = [str(h).strip() for h in (field.get("head", []) or [])]
    rows: List[List[Any]] = []

    for row in parsed:
        if isinstance(row, list):
            # Check if it's an empty placeholder row like ["", "", ""]
            if not any(str(x).strip() for x in row):
                continue
            rows.append([_format_table_val(cell) for cell in row])
        elif isinstance(row, dict):
            mapped_row: list[Any] = []
            if headers:
                for h in headers:
                    norm_h = normalize_key(h)
                    val = None
                    # Exact or normalized match in dict
                    for k, v in row.items():
                        if normalize_key(k) == norm_h:
                            val = v
                            break
                    # Synonym lookups if not found directly
                    if val is None or val == "":
                        if "costhead" in norm_h or norm_h in ["costhead", "head", "category", "service"]:
                            val = row.get("cost_head") or row.get("head") or row.get("category") or row.get("service") or row.get("item") or row.get("name")
                        elif "vendor" in norm_h:
                            val = row.get("vendor") or row.get("supplier") or row.get("vendor_basis") or row.get("basis")
                        elif "description" in norm_h:
                            val = row.get("cost_description") or row.get("description") or row.get("desc") or row.get("details") or row.get("service_cost_item") or row.get("service") or row.get("item")
                        elif "quantity" in norm_h or "qty" in norm_h:
                            val = row.get("quantity") or row.get("qty") or row.get("count") or (1 if row.get("rate") is not None or row.get("amount") is not None else "")
                        elif "rate" in norm_h:
                            val = row.get("rate") or row.get("unit_price") or row.get("unit_cost") or row.get("price")
                        elif norm_h == "amount":
                            val = row.get("amount") or row.get("cost") or row.get("subtotal") or row.get("actual")
                            if (val is None or val == "") and row.get("rate") is not None and row.get("quantity") is not None:
                                try:
                                    val = float(str(row["rate"]).replace(",", "").strip()) * float(str(row["quantity"]).replace(",", "").strip())
                                except (ValueError, TypeError):
                                    pass
                        elif "tax" in norm_h and "%" in h:
                            val = row.get("tax(%)") or row.get("tax_%") or row.get("tax_percent") or row.get("tax_pct") or row.get("tax_rate") or row.get("tax") or "7.45"
                        elif "taxamount" in norm_h or ("tax" in norm_h and "amount" in norm_h):
                            val = row.get("tax_amount") or row.get("tax_amt")
                            if (val is None or val == "") and row.get("amount") is not None:
                                try:
                                    tax_pct = float(str(row.get("tax_percent") or row.get("tax_pct") or row.get("tax") or 7.45).replace("%", "").strip())
                                    amt_val = float(str(row["amount"]).replace(",", "").strip())
                                    val = amt_val * (tax_pct / 100.0)
                                except (ValueError, TypeError):
                                    pass
                        elif norm_h in ["total", "totalamount", "grossamount"]:
                            val = row.get("total") or row.get("total_amount") or row.get("gross_amount")
                            if (val is None or val == "") and row.get("amount") is not None:
                                try:
                                    amt_val = float(str(row["amount"]).replace(",", "").strip())
                                    tax_amt_val = float(str(row.get("tax_amount") or (amt_val * 0.0745)).replace(",", "").strip())
                                    val = amt_val + tax_amt_val
                                except (ValueError, TypeError):
                                    pass

                    mapped_row.append(_format_table_val(val))
                rows.append(mapped_row)
            else:
                rows.append([_format_table_val(v) for v in row.values()])
        else:
            rows.append([_format_table_val(row)])

    return rows


def get_font_metrics(font_name: str, font_size: float) -> Tuple[float, float]:
    try:
        font = pdfmetrics.getFont(font_name)
        face = font.face
        ascent = (face.ascent / 1000.0) * font_size
        descent = abs(face.descent / 1000.0) * font_size
        return ascent, descent
    except Exception:
        return font_size * 0.8, font_size * 0.2


def measure_text_width(text: str, font_name: str, font_size: float, char_space: float = 0.0) -> float:
    if not text:
        return 0.0
    base_w = pdfmetrics.stringWidth(text, font_name, font_size)
    extra = max(0, len(text) - 1) * char_space
    return base_w + extra


def wrap_paragraph_text(
    paragraph: str,
    max_width: float,
    font_name: str,
    font_size: float,
    char_space: float,
) -> List[str]:
    p = normalize_text(paragraph)
    if p == "":
        return [""]
    words = p.split(" ")
    lines: List[str] = []
    current = ""

    for w in words:
        candidate = w if not current else f"{current} {w}"
        if current and measure_text_width(candidate, font_name, font_size, char_space) <= max_width:
            current = candidate
            continue
        if not current:
            if measure_text_width(w, font_name, font_size, char_space) <= max_width:
                current = w
            else:
                sub = ""
                for ch in w:
                    if measure_text_width(sub + ch, font_name, font_size, char_space) > max_width:
                        lines.append(sub)
                        sub = ch
                    else:
                        sub += ch
                current = sub
        else:
            lines.append(current)
            current = w

    if current != "" or not lines:
        lines.append(current)
    return lines


def build_wrapped_lines(
    text: str,
    max_width: float,
    font_name: str,
    font_size: float,
    char_space: float,
) -> List[Tuple[str, bool]]:
    p_list = normalize_text(text).split("\n")
    all_lines: List[Tuple[str, bool]] = []
    for para in p_list:
        wrapped = wrap_paragraph_text(para, max_width, font_name, font_size, char_space)
        for idx, line in enumerate(wrapped):
            all_lines.append((line, idx == len(wrapped) - 1))
    return all_lines


def compute_text_block_height(
    line_count: int,
    font_name: str,
    font_size: float,
    line_height_multiplier: float,
) -> Tuple[float, float, float, float]:
    ascent, descent = get_font_metrics(font_name, font_size)
    line_height = font_size * line_height_multiplier
    block_height = ascent + descent + max(0, line_count - 1) * line_height
    return ascent, descent, line_height, block_height


def is_grid_box_field(field: Dict[str, Any]) -> bool:
    name = str(field.get("name", ""))
    dk = str(field.get("dataKey", ""))

    # Exclude page-level titles and notes
    if name in [
        "CompanyName",
        "MainTitle",
        "DocumentTitle",
        "CompanyAddress",
        "CompanyContact",
        "PortCallCostDetailsHeader",
        "RemarksLabel",
        "Remarks",
        "FooterNote",
    ]:
        return False

    if name.startswith((
        "CustomerPrincipal",
        "DocumentNo",
        "ShipperCharterer",
        "DocumentDate",
        "VesselName",
        "Currency",
        "IMONumber",
        "PaymentTerms",
        "VoyageNumber",
        "CostVerified",
        "PortOfCall",
        "PortofCall",
        "RelatedPDAFDA",
        "Terminal",
        "ETAETD",
        "No",
        "ServiceCostItem",
        "VendorBasis",
        "Qty",
        "Rate",
        "AmountSGD",
        "AmountEUR",
        "AmountUSD",
        "Amount",
        "ActualCostTax",
        "LessPDAAdvance",
        "BalanceDue",
        "EstimatedSubtotal",
        "EstimatedTax",
        "TotalPDA",
    )):
        return True
    if dk in [
        "Customer / Principal",
        "Document No.",
        "Shipper / Charterer / Broker",
        "Document Date",
        "Vessel Name",
        "Currency",
        "IMO Number",
        "Payment Terms",
        "Voyage Number",
        "Cost Verified",
        "Port of Call",
        "Related PDA/FDA",
        "Terminal",
        "ETA / ETD",
        "Actual Cost + Tax",
        "Less PDA Advance",
        "BALANCE DUE",
        "Estimated Subtotal",
        "Estimated Tax (7.45%)",
        "TOTAL PDA",
    ]:
        return True
    if re.match(r"^(No\.|Service / Cost Item|Vendor / Basis|Qty|Rate|Amount.*)\s+\d+$", dk):
        return True
    return False


def draw_text_lines_in_box(
    c: canvas.Canvas,
    lines: Sequence[Tuple[str, bool]],
    x: float,
    y_bottom: float,
    width: float,
    height: float,
    field: Dict[str, Any],
    font_name: str,
    font_size: float,
    char_space: float,
    fill_color: Color,
    bg_color: Optional[Color],
    underline: bool,
    strikethrough: bool,
    line_height_multiplier: float,
    stroke_color: Optional[Color] = None,
    stroke_width: float = 0.0,
) -> None:
    if bg_color is not None or stroke_color is not None:
        c.saveState()
        if bg_color is not None:
            c.setFillColor(bg_color)
        if stroke_color is not None and stroke_width > 0:
            c.setStrokeColor(stroke_color)
            c.setLineWidth(stroke_width)
            c.rect(x, y_bottom, width, height, fill=1 if bg_color else 0, stroke=1)
        elif bg_color is not None:
            c.rect(x, y_bottom, width, height, fill=1, stroke=0)
        c.restoreState()

    inner_padding = parse_inner_padding_points(field.get("padding", 0))
    field_name = str(field.get("name", ""))

    if is_grid_box_field(field):
        if field_name.endswith("Header"):
            if inner_padding["left"] == 0:
                inner_padding["left"] = 1.0
            if inner_padding["right"] == 0:
                inner_padding["right"] = 1.0
        else:
            if inner_padding["left"] == 0:
                inner_padding["left"] = 3.0
            if inner_padding["right"] == 0:
                inner_padding["right"] = 3.0

    inner_x = x + inner_padding["left"]
    inner_y_bottom = y_bottom + inner_padding["bottom"]
    inner_width = max(0.0, width - inner_padding["left"] - inner_padding["right"])
    inner_height = max(0.0, height - inner_padding["top"] - inner_padding["bottom"])

    c.saveState()
    clip_path = c.beginPath()
    clip_path.rect(x + 0.25, y_bottom + 0.25, max(0.0, width - 0.5), max(0.0, height - 0.5))
    c.clipPath(clip_path, stroke=0, fill=0)

    if lines:
        align = (field.get("alignment", "left") or "left").lower()
        valign = (field.get("verticalAlignment", "middle") or "middle").lower()
        if is_grid_box_field(field):
            valign = "middle"

        # Auto-scale font size if needed for single-line text or headers so they never get clipped
        if len(lines) == 1 and inner_width > 0:
            raw_single_text = normalize_text(lines[0][0])
            needed_w = measure_text_width(raw_single_text, font_name, font_size, char_space)
            if needed_w > inner_width:
                scale_factor = inner_width / needed_w
                font_size = max(5.5, font_size * scale_factor * 0.98)

        ascent, descent, line_height, block_height = compute_text_block_height(
            len(lines), font_name, font_size, line_height_multiplier
        )

        box_top = inner_y_bottom + inner_height
        if valign in {"middle", "center"}:
            text_top = inner_y_bottom + (inner_height + block_height) / 2.0
        elif valign == "bottom":
            text_top = inner_y_bottom + block_height
        else:
            text_top = box_top

        baseline_y = text_top - ascent

        for line_text, is_last_in_p in lines:
            line_text = normalize_text(line_text)
            base_w = measure_text_width(line_text, font_name, font_size, char_space)
            text_x = inner_x
            word_space = 0.0

            if align == "center":
                text_x = inner_x + max(0.0, (inner_width - base_w) / 2.0)
            elif align == "right":
                text_x = inner_x + max(0.0, inner_width - base_w)
            elif align == "justify" and not is_last_in_p:
                sc = line_text.count(" ")
                rem = inner_width - base_w
                if sc > 0 and rem > 0:
                    word_space = rem / sc

            text_obj = c.beginText()
            text_obj.setTextOrigin(text_x, baseline_y)
            text_obj.setFont(font_name, font_size)
            text_obj.setFillColor(fill_color)

            if char_space:
                try:
                    text_obj.setCharSpace(char_space)
                except AttributeError:
                    pass
            if word_space:
                try:
                    text_obj.setWordSpace(word_space)
                except AttributeError:
                    pass

            text_obj.textLine(line_text)
            c.drawText(text_obj)

            # Decorations
            rendered_w = base_w + (line_text.count(" ") * word_space)
            if underline and rendered_w > 0:
                c.setStrokeColor(fill_color)
                c.setLineWidth(max(0.4, font_size * 0.04))
                c.line(text_x, baseline_y - (font_size * 0.12), text_x + rendered_w, baseline_y - (font_size * 0.12))
            if strikethrough and rendered_w > 0:
                c.setStrokeColor(fill_color)
                c.setLineWidth(max(0.4, font_size * 0.04))
                c.line(text_x, baseline_y + (font_size * 0.28), text_x + rendered_w, baseline_y + (font_size * 0.28))

            baseline_y -= line_height

    c.restoreState()


def render_text_box(
    c: canvas.Canvas,
    text: str,
    x: float,
    y_bottom: float,
    width: float,
    height: float,
    field: Dict[str, Any],
) -> None:
    font_size = safe_float(field.get("fontSize", 10), 10.0)
    line_height_multiplier = safe_float(field.get("lineHeight", 1.15), 1.15)

    raw_font_color = str(field.get("fontColor", "")).strip().lower()
    raw_bg_color = str(field.get("backgroundColor", "")).strip().lower()
    field_name = str(field.get("name", ""))

    # Bold font determination
    is_bold = (
        field.get("fontWeight") in ["bold", "700", "800", "900"]
        or field.get("bold") is True
        or "bold" in str(field.get("fontName", "")).lower()
        or field_name in [
            "MainTitle",
            "DocumentTitle",
            "CompanyName",
            "PortCallCostDetailsHeader",
            "BalanceDueLabel",
            "BalanceDueValue",
            "TotalPDALabel",
            "TotalPDAValue",
            "NoHeader",
            "ServiceCostItemHeader",
            "VendorBasisHeader",
            "QtyHeader",
            "RateHeader",
            "AmountSGDHeader",
            "AmountEURHeader",
            "AmountUSDHeader",
            "AmountHeader",
        ]
    )

    if is_bold:
        font_name = "Helvetica-Bold"
    else:
        font_name = resolve_font_name(field.get("fontName", "Helvetica"))

    # 1. White on Deep Navy for header bars and BALANCE DUE / TOTAL PDA
    if field_name in [
        "BalanceDueLabel",
        "BalanceDueValue",
        "TotalPDALabel",
        "TotalPDAValue",
        "NoHeader",
        "ServiceCostItemHeader",
        "VendorBasisHeader",
        "QtyHeader",
        "RateHeader",
        "AmountSGDHeader",
        "AmountEURHeader",
        "AmountUSDHeader",
        "AmountHeader",
    ]:
        fill_color = colors.white
        bg_color = hex_to_color("#0B3557")
    # 2. Crisp Dark Charcoal / Black for dynamic values & table cells (replacing placeholder blue #1F5FBF)
    elif raw_font_color in ["#1f5fbf", "#1e60bf", "#1976d2", "#2563eb"] or (
        not is_static_label_field(field) and raw_font_color in ["", "#000000", "#111827", "#333333"]
    ):
        fill_color = hex_to_color("#111827")
        bg_color = hex_to_color("#FFFFFF") if raw_bg_color in ["#f8fbff", "#f0f4f8", ""] else hex_to_color(raw_bg_color)
    # 3. Deep Navy for static labels
    elif is_static_label_field(field):
        fill_color = hex_to_color("#0B3557")
        if is_grid_box_field(field):
            bg_color = hex_to_color("#EEF3F7")
        else:
            bg_color = hex_to_color(raw_bg_color) if raw_bg_color else None
    else:
        fill_color = hex_to_color(field.get("fontColor"), colors.black) or colors.black
        bg_color = hex_to_color(field.get("backgroundColor"))

    underline = bool(field.get("underline", False))
    strikethrough = bool(field.get("strikethrough", False))
    char_space = safe_float(field.get("characterSpacing", 0), 0.0)

    # 4. Crisp borders for grid/table boxes
    stroke_color = None
    stroke_width = 0.0
    if is_grid_box_field(field):
        if bg_color == hex_to_color("#0B3557"):
            stroke_color = hex_to_color("#0B3557")
            stroke_width = 0.5
        else:
            stroke_color = hex_to_color("#CBD5E1")
            stroke_width = 0.5
            if bg_color is None and not is_static_label_field(field):
                bg_color = hex_to_color("#FFFFFF")

    inner_padding = parse_inner_padding_points(field.get("padding", 0))
    inner_width = max(0.0, width - inner_padding["left"] - inner_padding["right"])

    lines = build_wrapped_lines(text, inner_width, font_name, font_size, char_space)
    draw_text_lines_in_box(
        c=c,
        lines=lines,
        x=x,
        y_bottom=y_bottom,
        width=width,
        height=height,
        field=field,
        font_name=font_name,
        font_size=font_size,
        char_space=char_space,
        fill_color=fill_color,
        bg_color=bg_color,
        underline=underline,
        strikethrough=strikethrough,
        line_height_multiplier=line_height_multiplier,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
    )


def compute_column_widths(total_width: float, percentages: Sequence[Any], col_count: int) -> List[float]:
    if percentages and len(percentages) == col_count:
        nums = [safe_float(v, 0.0) for v in percentages]
        p_sum = sum(nums)
        if p_sum > 0:
            return [total_width * (p / p_sum) for p in nums]
    if col_count <= 0:
        return [total_width]
    return [total_width / col_count] * col_count


def build_paragraph_style(style_name: str, style_dict: Dict[str, Any]) -> ParagraphStyle:
    font_name = resolve_font_name(style_dict.get("fontName", "Helvetica"))
    font_size = safe_float(style_dict.get("fontSize", 9), 9.0)
    lh = safe_float(style_dict.get("lineHeight", 1.2), 1.2)
    text_col = hex_to_color(style_dict.get("fontColor"), colors.black) or colors.black

    return ParagraphStyle(
        name=style_name,
        fontName=font_name,
        fontSize=font_size,
        leading=font_size * lh,
        textColor=text_col,
        alignment=alignment_to_ta(style_dict.get("alignment", "left")),
        spaceBefore=0,
        spaceAfter=0,
    )


def render_table_box(
    c: canvas.Canvas,
    field: Dict[str, Any],
    data: Dict[str, Any],
    x: float,
    y_bottom: float,
    width: float,
    height: float,
) -> None:
    headers: List[str] = [str(h) for h in field.get("head", []) or []]
    rows = resolve_table_rows(field, data)
    col_count = len(headers) if headers else (len(rows[0]) if rows else 1)

    col_widths = compute_column_widths(width, field.get("headWidthPercentages", []), col_count)
    head_style = field.get("headStyles", {}) or {}
    body_style = field.get("bodyStyles", {}) or {}
    column_styles = field.get("columnStyles", {}) or {}

    # Determine optimal font sizes and paddings based on table column density
    if col_count >= 8:
        default_th_size = 7.5
        default_td_size = 7.5
        th_lh = 1.15
        td_lh = 1.15
        pad_top_bottom = 3.0
        pad_left_right = 2.5
    elif col_count >= 6:
        default_th_size = 8.0
        default_td_size = 8.0
        th_lh = 1.15
        td_lh = 1.15
        pad_top_bottom = 3.5
        pad_left_right = 3.0
    else:
        default_th_size = safe_float(head_style.get("fontSize", 9.0), 9.0)
        default_td_size = safe_float(body_style.get("fontSize", 9.0), 9.0)
        th_lh = safe_float(head_style.get("lineHeight", 1.15), 1.15)
        td_lh = safe_float(body_style.get("lineHeight", 1.15), 1.15)
        pad_top_bottom = 3.5
        pad_left_right = 4.0

    # Determine numeric columns for right-alignment
    numeric_col_indices: set[int] = set()
    for idx, h in enumerate(headers):
        norm_h = normalize_key(h)
        if any(term in norm_h for term in ["quantity", "qty", "rate", "amount", "tax", "total", "price"]):
            numeric_col_indices.add(idx)

    table_data: list[list[Any]] = []
    style_commands: list[Tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad_top_bottom),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_top_bottom),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_left_right),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_left_right),
    ]

    row_idx = 0
    # Header Row
    if headers and bool(field.get("showHead", True)):
        h_row: list[Any] = []
        for c_idx, h_text in enumerate(headers):
            c_style_dict = deep_merge(head_style, column_styles.get(str(c_idx), column_styles.get(c_idx, {})))
            c_style_dict["fontName"] = "Helvetica-Bold"
            c_style_dict["fontSize"] = min(safe_float(c_style_dict.get("fontSize", default_th_size)), default_th_size)
            c_style_dict["lineHeight"] = th_lh
            if c_idx in numeric_col_indices:
                c_style_dict["alignment"] = "right"
            p_style = build_paragraph_style(f"th_{id(field)}_{c_idx}", c_style_dict)
            h_row.append(Paragraph(escape(str(h_text)), p_style))
        table_data.append(h_row)

        h_bg = hex_to_color(head_style.get("backgroundColor", "#2980ba")) or hex_to_color("#2980ba")
        style_commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), h_bg))
        row_idx += 1

    # Data Rows
    for r_num, row_vals in enumerate(rows):
        d_row: list[Any] = []
        for c_idx in range(col_count):
            val = row_vals[c_idx] if c_idx < len(row_vals) else ""
            c_style_dict = deep_merge(body_style, column_styles.get(str(c_idx), column_styles.get(c_idx, {})))
            c_style_dict["fontName"] = resolve_font_name(c_style_dict.get("fontName", "Helvetica"))
            c_style_dict["fontSize"] = min(safe_float(c_style_dict.get("fontSize", default_td_size)), default_td_size)
            c_style_dict["lineHeight"] = td_lh
            if c_idx in numeric_col_indices:
                c_style_dict["alignment"] = "right"
            p_style = build_paragraph_style(f"td_{id(field)}_{r_num}_{c_idx}", c_style_dict)
            d_row.append(Paragraph(escape(str(val)), p_style))
        table_data.append(d_row)

        b_bg = hex_to_color(body_style.get("backgroundColor"))
        alt_bg = hex_to_color(body_style.get("alternateBackgroundColor", "#f5f5f5"))
        if b_bg:
            style_commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), b_bg))
        elif alt_bg and r_num % 2 == 1:
            style_commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), alt_bg))
        row_idx += 1

    # Border & Grid
    border_col = hex_to_color(field.get("tableStyles", {}).get("borderColor") or body_style.get("borderColor") or "#CBD5E1") or colors.HexColor("#CBD5E1")
    border_w = safe_float(field.get("tableStyles", {}).get("borderWidth", 0.4), 0.4)
    style_commands.append(("GRID", (0, 0), (-1, -1), border_w, border_col))

    if not table_data:
        return

    t = Table(table_data, colWidths=col_widths)
    t.setStyle(TableStyle(style_commands))
    _, actual_h = t.wrapOn(c, width, height)

    t.drawOn(c, x, y_bottom + height - actual_h)


def build_box_coordinates(
    field: Dict[str, Any],
    page_w_pts: float,
    page_h_pts: float,
    unit: str,
    base_padding: Dict[str, float],
    apply_padding: bool = False,
) -> Tuple[float, float, float, float]:
    pos = field.get("position", {}) or {}
    x = convert_page_measure(pos.get("x", 0), unit)
    y_top = convert_page_measure(pos.get("y", 0), unit)
    w = convert_page_measure(field.get("width", 0), unit)
    h = convert_page_measure(field.get("height", 0), unit)

    x_off = base_padding["left"] if apply_padding else 0.0
    y_off = base_padding["top"] if apply_padding else 0.0

    x_pt = x + x_off
    top_y = page_h_pts - y_top - y_off
    y_bottom = top_y - h
    return x_pt, y_bottom, w, h


def render_schema_page(
    c: canvas.Canvas,
    schema_fields: Sequence[Dict[str, Any]],
    data: Dict[str, Any],
    page_w_pts: float,
    page_h_pts: float,
    page_unit: str,
    base_padding: Dict[str, float],
) -> None:
    for field in schema_fields:
        if not isinstance(field, dict):
            continue

        f_type = field.get("type", "text")
        x, y_bottom, w, h = build_box_coordinates(field, page_w_pts, page_h_pts, page_unit, base_padding)
        rot = safe_float(field.get("rotate", 0), 0.0)
        op = safe_float(field.get("opacity", 1.0), 1.0)

        c.saveState()
        apply_opacity(c, op)

        if rot:
            c.translate(x, y_bottom + h)
            c.rotate(-rot)
            cur_x, cur_y = 0.0, -h
        else:
            cur_x, cur_y = x, y_bottom

        if f_type in {"text", "multiVariableText", "smartText"}:
            txt = resolve_text_content(field, data)
            render_text_box(c, txt, cur_x, cur_y, w, h, field)
        elif f_type == "table":
            render_table_box(c, field, data, cur_x, cur_y, w, h)
        elif f_type == "line":
            stroke_col = hex_to_color(field.get("color", field.get("fontColor", "#000000"))) or colors.black
            c.setStrokeColor(stroke_col)
            c.setLineWidth(max(0.5, safe_float(field.get("lineWidth", 1.0))))
            c.line(cur_x, cur_y + h / 2.0, cur_x + w, cur_y + h / 2.0)
        elif f_type == "rectangle":
            fill_col = hex_to_color(field.get("backgroundColor"))
            stroke_col = hex_to_color(field.get("borderColor", field.get("color")))
            c.setFillColor(fill_col or colors.transparent)
            c.setStrokeColor(stroke_col or colors.black)
            c.setLineWidth(safe_float(field.get("borderWidth", 0.5)))
            c.rect(cur_x, cur_y, w, h, fill=1 if fill_col else 0, stroke=1 if stroke_col else 0)
        elif f_type == "image":
            img_src = resolve_metadata_value(field, data, field.get("content"))
            if img_src and isinstance(img_src, str):
                try:
                    if "base64," in img_src:
                        img_bytes = base64.b64decode(img_src.split("base64,", 1)[1])
                        c.drawImage(ImageReader(io.BytesIO(img_bytes)), cur_x, cur_y, width=w, height=h, preserveAspectRatio=True)
                    elif os.path.isfile(img_src):
                        c.drawImage(ImageReader(img_src), cur_x, cur_y, width=w, height=h, preserveAspectRatio=True)
                except Exception as exc:
                    logger.warning("template_image_render_failed", extra={"error": str(exc)})

        c.restoreState()


def measure_table_height_mm(
    field: Dict[str, Any],
    rows: List[List[Any]],
    width_pts: float,
) -> float:
    headers: List[str] = [str(h) for h in field.get("head", []) or []]
    col_count = len(headers) if headers else (len(rows[0]) if rows else 1)
    col_widths = compute_column_widths(width_pts, field.get("headWidthPercentages", []), col_count)
    head_style = field.get("headStyles", {}) or {}
    body_style = field.get("bodyStyles", {}) or {}
    column_styles = field.get("columnStyles", {}) or {}

    if col_count >= 8:
        default_th_size = 7.5
        default_td_size = 7.5
        th_lh = 1.15
        td_lh = 1.15
        pad_tb = 3.0
        pad_lr = 2.5
    elif col_count >= 6:
        default_th_size = 8.0
        default_td_size = 8.0
        th_lh = 1.15
        td_lh = 1.15
        pad_tb = 3.5
        pad_lr = 3.0
    else:
        default_th_size = safe_float(head_style.get("fontSize", 9.0), 9.0)
        default_td_size = safe_float(body_style.get("fontSize", 9.0), 9.0)
        th_lh = safe_float(head_style.get("lineHeight", 1.15), 1.15)
        td_lh = safe_float(body_style.get("lineHeight", 1.15), 1.15)
        pad_tb = 3.5
        pad_lr = 4.0

    numeric_col_indices: set[int] = set()
    for idx, h in enumerate(headers):
        norm_h = normalize_key(h)
        if any(term in norm_h for term in ["quantity", "qty", "rate", "amount", "tax", "total", "price"]):
            numeric_col_indices.add(idx)

    table_data: list[list[Any]] = []
    if headers and bool(field.get("showHead", True)):
        h_row: list[Any] = []
        for c_idx, h_text in enumerate(headers):
            c_style = deep_merge(head_style, column_styles.get(str(c_idx), {}))
            c_style["fontName"] = "Helvetica-Bold"
            c_style["fontSize"] = min(safe_float(c_style.get("fontSize", default_th_size)), default_th_size)
            c_style["lineHeight"] = th_lh
            if c_idx in numeric_col_indices:
                c_style["alignment"] = "right"
            p_style = build_paragraph_style(f"m_th_{c_idx}", c_style)
            h_row.append(Paragraph(escape(str(h_text)), p_style))
        table_data.append(h_row)

    for r_num, row_vals in enumerate(rows):
        d_row: list[Any] = []
        for c_idx in range(col_count):
            val = row_vals[c_idx] if c_idx < len(row_vals) else ""
            c_style = deep_merge(body_style, column_styles.get(str(c_idx), {}))
            c_style["fontName"] = resolve_font_name(c_style.get("fontName", "Helvetica"))
            c_style["fontSize"] = min(safe_float(c_style.get("fontSize", default_td_size)), default_td_size)
            c_style["lineHeight"] = td_lh
            if c_idx in numeric_col_indices:
                c_style["alignment"] = "right"
            p_style = build_paragraph_style(f"m_td_{r_num}_{c_idx}", c_style)
            d_row.append(Paragraph(escape(str(val)), p_style))
        table_data.append(d_row)

    if not table_data:
        return 0.0

    t = Table(table_data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), pad_tb),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_tb),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_lr),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_lr),
    ]))
    _, actual_h_pts = t.wrap(width_pts, 2000.0)
    return actual_h_pts / mm


def extract_port_call_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extracts all line items from either an array or numbered dictionary keys."""
    items: List[Dict[str, Any]] = []

    # 1. Check array keys
    for ak in [
        "items",
        "disbursement_items",
        "estimated_costs",
        "costs",
        "services",
        "records",
        "cost_items",
        "port_call_costs",
        "cost_details",
        "Cost Details",
    ]:
        val = data.get(ak)
        if isinstance(val, list) and val:
            for idx, item in enumerate(val, start=1):
                if isinstance(item, dict):
                    no_val = str(item.get("no") or item.get("code") or idx)
                    srv = str(
                        item.get("service")
                        or item.get("service_cost_item")
                        or item.get("cost_head")
                        or item.get("item")
                        or item.get("description")
                        or item.get("name")
                        or ""
                    )
                    vnd = str(
                        item.get("vendor")
                        or item.get("basis")
                        or item.get("vendor_basis")
                        or item.get("tariff")
                        or item.get("notes")
                        or ""
                    )
                    qty = str(
                        item.get("qty")
                        or item.get("quantity")
                        or item.get("count")
                        or (1 if item.get("rate") is not None else "")
                    )
                    rate = (
                        item.get("rate")
                        or item.get("unit_price")
                        or item.get("unit_cost")
                        or item.get("tariff")
                        or ""
                    )
                    rate_str = (
                        f"{float(rate):,.2f}"
                        if isinstance(rate, (int, float))
                        else str(rate)
                    )
                    amt = (
                        item.get("amount")
                        or item.get("total")
                        or item.get("actual")
                        or item.get("cost")
                        or ""
                    )
                    amt_str = (
                        f"{float(amt):,.2f}"
                        if isinstance(amt, (int, float))
                        else str(amt)
                    )
                    if srv or amt_str:
                        items.append(
                            {
                                "no": no_val,
                                "service": srv,
                                "vendor": vnd,
                                "qty": qty,
                                "rate": rate_str,
                                "amount": amt_str,
                            }
                        )
                elif isinstance(item, list) and len(item) >= 6:
                    # 2D list item: e.g. ["Port Dues", "PSA", "Dues", 1, 4500, 4500, ...]
                    items.append(
                        {
                            "no": str(idx),
                            "service": str(item[0]),
                            "vendor": str(item[1]),
                            "qty": str(item[3]) if len(item) > 3 else "",
                            "rate": str(item[4]) if len(item) > 4 else "",
                            "amount": str(item[5]) if len(item) > 5 else "",
                        }
                    )
            if items:
                return items

    # 2. Check numbered keys up to 200
    for i in range(1, 201):
        srv = str(
            data.get(
                f"Service / Cost Item {i}",
                data.get(f"ServiceCostItem{i}", data.get(f"service_{i}", "")),
            )
        ).strip()
        vnd = str(
            data.get(
                f"Vendor / Basis {i}",
                data.get(f"VendorBasis{i}", data.get(f"vendor_{i}", "")),
            )
        ).strip()
        qty = str(
            data.get(f"Qty {i}", data.get(f"Qty{i}", data.get(f"qty_{i}", "")))
        ).strip()
        rate = data.get(f"Rate {i}", data.get(f"Rate{i}", data.get(f"rate_{i}", "")))
        rate_str = (
            f"{float(rate):,.2f}"
            if isinstance(rate, (int, float))
            else str(rate).strip()
            if rate is not None
            else ""
        )
        amt = data.get(
            f"Amount (SGD) {i}",
            data.get(
                f"Amount (EUR) {i}",
                data.get(
                    f"Amount (USD) {i}",
                    data.get(
                        f"Amount {i}",
                        data.get(
                            f"AmountSGD{i}", data.get(f"amount_{i}", "")
                        ),
                    ),
                ),
            ),
        )
        amt_str = (
            f"{float(amt):,.2f}"
            if isinstance(amt, (int, float))
            else str(amt).strip()
            if amt is not None
            else ""
        )
        no_val = str(
            data.get(f"No. {i}", data.get(f"No {i}", data.get(f"no_{i}", i)))
        ).strip()

        if srv or (f"Service / Cost Item {i}" in data) or amt_str:
            items.append(
                {
                    "no": no_val,
                    "service": srv,
                    "vendor": vnd,
                    "qty": qty,
                    "rate": rate_str,
                    "amount": amt_str,
                }
            )

    return items


def render_pdfme_template_to_pdf(
    template: Dict[str, Any],
    data: Dict[str, Any],
    output_path: str,
    title: Optional[str] = None,
) -> Tuple[str, bytes, int]:
    """Renders a complete pdfme template JSON into a PDF with multi-page continuation support."""
    base_pdf = template.get("basePdf", {}) or {}
    page_w_val = safe_float(base_pdf.get("width", 210), 210.0)
    page_h_val = safe_float(base_pdf.get("height", 297), 297.0)

    unit = detect_page_unit(page_w_val, page_h_val)
    page_w_pts = convert_page_measure(page_w_val, unit)
    page_h_pts = convert_page_measure(page_h_val, unit)

    base_padding = parse_page_padding(base_pdf.get("padding"), unit)
    schemas: List[List[Dict[str, Any]]] = template.get("schemas", [])

    if not schemas:
        schemas = [[]]

    c = canvas.Canvas(output_path, pagesize=(page_w_pts, page_h_pts))
    if title:
        c.setTitle(title)

    enriched_data = auto_flatten_and_enrich_data(data)

    # 1. Check if the template contains a dynamic table element (e.g. new PDA format)
    table_field = next((el for el in schemas[0] if el.get("type") == "table"), None)

    if len(schemas) == 1 and table_field:
        table_w_pts = convert_page_measure(table_field.get("width", 186.8), unit)
        all_table_rows = resolve_table_rows(table_field, enriched_data)
        table_start_y = safe_float(table_field.get("position", {}).get("y", 199.69), 199.69)
        base_schema = schemas[0]

        summary_names = {
            "EstimatedSubtotalLabel",
            "EstimatedSubtotalValue",
            "EstimatedTaxLabel",
            "EstimatedTaxValue",
            "TotalPDALabel",
            "TotalPDAValue",
            "RemarksLabel",
            "Remarks",
            "FooterNote",
        }
        metadata_names = {
            "CustomerPrincipalLabel",
            "CustomerPrincipalValue",
            "DocumentNoLabel",
            "JobNoValue",
            "ShipperChartererBrokerLabel",
            "ShipperChartererBrokerValue",
            "DocumentDateLabel",
            "AgencyAppointmentDateValue",
            "VesselNameLabel",
            "VesselNameValue",
            "CurrencyLabel",
            "CurrencyValue",
            "IMONumberLabel",
            "IMONumberValue",
            "PaymentTermsLabel",
            "PaymentTermsValue",
            "VoyageNumberLabel",
            "VoyageNumberValue",
            "CostVerifiedLabel",
            "CostVerifiedValue",
            "PortOfCallLabel",
            "PortofCallValue",
            "RelatedPDAFDALabel",
            "RelatedPDA/FDAValue",
            "TerminalLabel",
            "TerminalBerthValue",
            "ETAETDLabel",
            "ETAValue",
            "ETDValue",
            "AgencyAppointmentDateLabel",
            "CargoTypeLabel",
            "CargoTypeValue",
            "CargoQuantityLabel",
            "CargoQuantityValue",
            "CargoDescriptionLabel",
            "CargoDescriptionValue",
        }

        # Check if all rows fit on a single page with the summary block
        total_table_h_mm = measure_table_height_mm(table_field, all_table_rows, table_w_pts)
        max_single_page_h_mm = 246.0 - table_start_y  # ~46.3 mm (leaving room for summary starting at 249mm)

        if total_table_h_mm > max_single_page_h_mm and len(all_table_rows) > 1:
            # Dynamic partition across multiple pages
            page_chunks: List[List[List[Any]]] = []

            # Page 1: allow up to max_p1_h
            p1_max_h = 244.0 - table_start_y  # ~44.3 mm
            p1_rows: List[List[Any]] = []
            for r in all_table_rows:
                test_rows = p1_rows + [r]
                if measure_table_height_mm(table_field, test_rows, table_w_pts) <= p1_max_h or len(p1_rows) == 0:
                    p1_rows.append(r)
                else:
                    break

            page_chunks.append(p1_rows)
            remaining_rows = all_table_rows[len(p1_rows):]

            # Continuation Pages (starts at y=55mm):
            cont_start_y = 55.0
            max_middle_cont_h = 262.0 - cont_start_y  # ~207 mm (~18-20 rows)
            max_last_cont_h = 225.0 - cont_start_y    # ~170 mm (~14-16 rows to fit summary + remarks)

            while remaining_rows:
                test_h = measure_table_height_mm(table_field, remaining_rows, table_w_pts)
                if test_h <= max_last_cont_h:
                    page_chunks.append(remaining_rows)
                    break
                else:
                    cont_rows: List[List[Any]] = []
                    for r in remaining_rows:
                        test_rows = cont_rows + [r]
                        if measure_table_height_mm(table_field, test_rows, table_w_pts) <= max_middle_cont_h or len(cont_rows) == 0:
                            cont_rows.append(r)
                        else:
                            break
                    page_chunks.append(cont_rows)
                    remaining_rows = remaining_rows[len(cont_rows):]

            num_pages = len(page_chunks)

            for page_idx, page_table_rows in enumerate(page_chunks):
                if page_idx > 0:
                    c.showPage()

                is_first_page = (page_idx == 0)
                is_last_page = (page_idx == num_pages - 1)

                page_data = dict(enriched_data)
                table_key = table_field.get("dataKey") or table_field.get("name") or "Cost Details"
                page_data[table_key] = page_table_rows

                # Calculate sum of amounts on this page
                page_amt_sum = 0.0
                for r in page_table_rows:
                    if len(r) >= 6:
                        raw_amt = str(r[5]).replace(",", "").strip()
                        try:
                            page_amt_sum += float(raw_amt)
                        except ValueError:
                            pass

                currency_str = str(enriched_data.get("Currency", enriched_data.get("currency", "USD"))).strip()

                if is_last_page:
                    page_data["FooterNote"] = (
                        f"Page {page_idx + 1} of {num_pages} - Final Summary | Sample document for Vessel Call workflow."
                    )
                else:
                    page_data["FooterNote"] = (
                        f"Page {page_idx + 1} of {num_pages} - Continued on Page {page_idx + 2}..."
                    )

                page_schema: List[Dict[str, Any]] = []

                if is_first_page:
                    for el in base_schema:
                        el_name = el.get("name", "")
                        if el_name in summary_names and el_name != "FooterNote":
                            continue
                        el_copy = dict(el)
                        if el_name == table_key or el.get("type") == "table":
                            el_copy["content"] = page_table_rows
                        page_schema.append(el_copy)

                    # Dynamic Carried Forward Block on page 1
                    p1_table_h = measure_table_height_mm(table_field, page_table_rows, table_w_pts)
                    p1_table_bottom_y = table_start_y + p1_table_h
                    cf_y = p1_table_bottom_y + 3.0
                    notice_y = cf_y + 11.0

                    carried_amt_str = f"{currency_str} {page_amt_sum:,.2f}" if currency_str else f"{page_amt_sum:,.2f}"
                    page_schema.append(
                        {
                            "name": "CarriedForwardLabel",
                            "type": "text",
                            "content": "SUBTOTAL CARRIED FORWARD",
                            "position": {"x": 112, "y": cf_y},
                            "width": 48,
                            "height": 8,
                            "fontSize": 7.5,
                            "lineHeight": 1.15,
                            "fontColor": "#0B3557",
                            "backgroundColor": "#EEF3F7",
                            "alignment": "left",
                            "verticalAlignment": "middle",
                            "readOnly": True,
                            "fontName": "Helvetica-Bold",
                        }
                    )
                    page_schema.append(
                        {
                            "name": "CarriedForwardValue",
                            "type": "text",
                            "content": carried_amt_str,
                            "position": {"x": 160, "y": cf_y},
                            "width": 38,
                            "height": 8,
                            "fontSize": 8.5,
                            "lineHeight": 1.15,
                            "fontColor": "#111827",
                            "backgroundColor": "#FFFFFF",
                            "alignment": "right",
                            "verticalAlignment": "middle",
                            "readOnly": True,
                            "fontName": "Helvetica-Bold",
                        }
                    )
                    page_schema.append(
                        {
                            "name": "ContinuationNotice",
                            "type": "text",
                            "content": f"Document continues on Page 2 for remaining items, final tax calculation, and approval remarks.",
                            "position": {"x": 6, "y": notice_y},
                            "width": 192,
                            "height": 7,
                            "fontSize": 7.5,
                            "lineHeight": 1.15,
                            "fontColor": "#64748B",
                            "alignment": "center",
                            "verticalAlignment": "middle",
                            "readOnly": True,
                            "fontName": "Helvetica",
                        }
                    )
                else:
                    # Continuation Page: Header & Table shifted up
                    for el in base_schema:
                        el_name = el.get("name", "")
                        if el_name in metadata_names:
                            continue
                        if not is_last_page and el_name in summary_names and el_name != "FooterNote":
                            continue

                        el_copy = dict(el)

                        if el_name == "DocumentTitle":
                            orig_content = el_copy.get("content", "PROFORMA DISBURSEMENT ACCOUNT")
                            el_copy["content"] = f"{orig_content} - CONTINUATION SHEET"
                            el_copy["width"] = 120
                            el_copy["position"] = {"x": 80, "y": 34}
                            el_copy["alignment"] = "right"
                            page_schema.append(el_copy)
                            continue

                        if el_name == "PortCallCostDetailsHeader":
                            el_copy["position"] = {"x": 6, "y": 48}
                            page_schema.append(el_copy)
                            continue

                        if el_name == table_key or el.get("type") == "table":
                            el_copy["position"] = {"x": 10, "y": 55}
                            el_copy["content"] = page_table_rows
                            page_schema.append(el_copy)
                            continue

                        if is_last_page and el_name in summary_names and el_name != "FooterNote":
                            # Dynamic placement below continuation table using exact wrapped table height
                            actual_table_h_mm = measure_table_height_mm(table_field, page_table_rows, table_w_pts)
                            table_bottom_y = 55.0 + actual_table_h_mm
                            summary_start_y = max(135.0, table_bottom_y + 4.0)

                            if el_name in ["EstimatedSubtotalLabel", "EstimatedSubtotalValue"]:
                                el_copy["position"] = {"x": el["position"]["x"], "y": summary_start_y}
                            elif el_name in ["EstimatedTaxLabel", "EstimatedTaxValue"]:
                                el_copy["position"] = {"x": el["position"]["x"], "y": summary_start_y + 8}
                            elif el_name in ["TotalPDALabel", "TotalPDAValue"]:
                                el_copy["position"] = {"x": el["position"]["x"], "y": summary_start_y + 16}
                            elif el_name == "RemarksLabel":
                                el_copy["position"] = {"x": 6, "y": summary_start_y + 26}
                            elif el_name == "Remarks":
                                el_copy["position"] = {"x": 6, "y": summary_start_y + 32}

                            page_schema.append(el_copy)
                            continue

                        page_schema.append(el_copy)

                    # Top compact reference bar
                    vessel_val = str(enriched_data.get("Vessel", enriched_data.get("Vessel Name", enriched_data.get("vessel_name", ""))))
                    doc_no_val = str(enriched_data.get("Job No", enriched_data.get("Document No.", enriched_data.get("call_number", ""))))
                    date_val = str(enriched_data.get("Agency Appointment Date", enriched_data.get("Document Date", enriched_data.get("document_date", ""))))
                    port_val = str(enriched_data.get("Port", enriched_data.get("Port of Call", enriched_data.get("port_name", ""))))

                    mini_header_content = f"Vessel: {vessel_val}   |   Job No: {doc_no_val}   |   Date: {date_val}   |   Port: {port_val}"
                    page_schema.append(
                        {
                            "name": "ContinuationRefHeader",
                            "type": "text",
                            "content": mini_header_content,
                            "position": {"x": 6, "y": 44},
                            "width": 192,
                            "height": 7,
                            "fontSize": 8.5,
                            "lineHeight": 1.15,
                            "fontColor": "#0B3557",
                            "backgroundColor": "#EEF3F7",
                            "alignment": "center",
                            "verticalAlignment": "middle",
                            "readOnly": True,
                            "fontName": "Helvetica-Bold",
                        }
                    )

                    # If not last page, add Carried Forward block on middle continuation pages
                    if not is_last_page:
                        cont_table_h = measure_table_height_mm(table_field, page_table_rows, table_w_pts)
                        cont_table_bottom_y = 55.0 + cont_table_h
                        cf_y = cont_table_bottom_y + 3.0
                        notice_y = cf_y + 11.0

                        carried_amt_str = f"{currency_str} {page_amt_sum:,.2f}" if currency_str else f"{page_amt_sum:,.2f}"
                        page_schema.append(
                            {
                                "name": "CarriedForwardLabel",
                                "type": "text",
                                "content": "SUBTOTAL CARRIED FORWARD",
                                "position": {"x": 112, "y": cf_y},
                                "width": 48,
                                "height": 8,
                                "fontSize": 7.5,
                                "lineHeight": 1.15,
                                "fontColor": "#0B3557",
                                "backgroundColor": "#EEF3F7",
                                "alignment": "left",
                                "verticalAlignment": "middle",
                                "readOnly": True,
                                "fontName": "Helvetica-Bold",
                            }
                        )
                        page_schema.append(
                            {
                                "name": "CarriedForwardValue",
                                "type": "text",
                                "content": carried_amt_str,
                                "position": {"x": 160, "y": cf_y},
                                "width": 38,
                                "height": 8,
                                "fontSize": 8.5,
                                "lineHeight": 1.15,
                                "fontColor": "#111827",
                                "backgroundColor": "#FFFFFF",
                                "alignment": "right",
                                "verticalAlignment": "middle",
                                "readOnly": True,
                                "fontName": "Helvetica-Bold",
                            }
                        )
                        page_schema.append(
                            {
                                "name": "ContinuationNotice",
                                "type": "text",
                                "content": f"Document continues on Page {page_idx + 2} for remaining items, final tax calculation, and approval remarks.",
                                "position": {"x": 6, "y": notice_y},
                                "width": 192,
                                "height": 7,
                                "fontSize": 7.5,
                                "lineHeight": 1.15,
                                "fontColor": "#64748B",
                                "alignment": "center",
                                "verticalAlignment": "middle",
                                "readOnly": True,
                                "fontName": "Helvetica",
                            }
                        )

                render_schema_page(
                    c,
                    page_schema,
                    page_data,
                    page_w_pts,
                    page_h_pts,
                    unit,
                    base_padding,
                )

            c.save()
            with open(output_path, "rb") as f:
                pdf_bytes = f.read()
            page_count = num_pages
            try:
                with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                    page_count = len(doc)
            except Exception:
                pass
            return output_path, pdf_bytes, page_count

    # 2. Multi-page continuation for legacy flattened port call templates with >8 items
    all_items = extract_port_call_items(enriched_data)
    rows_per_page = 8

    if len(schemas) == 1 and len(all_items) > rows_per_page:
        num_pages = (len(all_items) + rows_per_page - 1) // rows_per_page
        base_schema = schemas[0]

        summary_names = {
            "ActualCostTaxLabel",
            "ActualCostTaxValue",
            "LessPDAAdvanceLabel",
            "LessPDAAdvanceValue",
            "BalanceDueLabel",
            "BalanceDueValue",
            "EstimatedSubtotalLabel",
            "EstimatedSubtotalValue",
            "EstimatedTaxLabel",
            "EstimatedTaxValue",
            "TotalPDALabel",
            "TotalPDAValue",
            "RemarksLabel",
            "Remarks",
            "FooterNote",
        }
        metadata_names = {
            "CustomerPrincipalLabel",
            "CustomerPrincipalValue",
            "Customer/PrincipalValue",
            "DocumentNoLabel",
            "DocumentNoValue",
            "DocumentNo.Value",
            "JobNoValue",
            "ShipperChartererBrokerLabel",
            "ShipperChartererBrokerValue",
            "Shipper/Charterer/BrokerValue",
            "DocumentDateLabel",
            "DocumentDateValue",
            "VesselNameLabel",
            "VesselNameValue",
            "CurrencyLabel",
            "CurrencyValue",
            "IMONumberLabel",
            "IMONumberValue",
            "PaymentTermsLabel",
            "PaymentTermsValue",
            "VoyageNumberLabel",
            "VoyageNumberValue",
            "CostVerifiedLabel",
            "CostVerifiedValue",
            "PortOfCallLabel",
            "PortofCallValue",
            "RelatedPDAFDALabel",
            "RelatedPDAFDAValue",
            "RelatedPDA/FDAValue",
            "TerminalLabel",
            "TerminalValue",
            "ETAETDLabel",
            "ETAETDValue",
            "ETA/ETDValue",
        }

        for page_idx in range(num_pages):
            if page_idx > 0:
                c.showPage()

            is_first_page = page_idx == 0
            is_last_page = page_idx == num_pages - 1

            start_idx = page_idx * rows_per_page
            end_idx = min(len(all_items), (page_idx + 1) * rows_per_page)
            page_items = all_items[start_idx:end_idx]

            page_data = dict(enriched_data)

            # Calculate sum for this page
            page_amt_sum = 0.0
            for it in page_items:
                raw_amt = str(it.get("amount", "")).replace(",", "").strip()
                try:
                    page_amt_sum += float(raw_amt)
                except ValueError:
                    pass

            currency_str = str(enriched_data.get("Currency", enriched_data.get("currency", "SGD"))).strip()

            # Map page items into row slots 1..8
            for slot_idx in range(1, rows_per_page + 1):
                if slot_idx <= len(page_items):
                    it = page_items[slot_idx - 1]
                    page_data[f"No. {slot_idx}"] = it["no"]
                    page_data[f"Service / Cost Item {slot_idx}"] = it["service"]
                    page_data[f"Vendor / Basis {slot_idx}"] = it["vendor"]
                    page_data[f"Qty {slot_idx}"] = it["qty"]
                    page_data[f"Rate {slot_idx}"] = it["rate"]
                    page_data[f"Amount (SGD) {slot_idx}"] = it["amount"]
                    page_data[f"Amount (EUR) {slot_idx}"] = it["amount"]
                    page_data[f"Amount (USD) {slot_idx}"] = it["amount"]
                    page_data[f"Amount {slot_idx}"] = it["amount"]
                else:
                    page_data[f"No. {slot_idx}"] = ""
                    page_data[f"Service / Cost Item {slot_idx}"] = ""
                    page_data[f"Vendor / Basis {slot_idx}"] = ""
                    page_data[f"Qty {slot_idx}"] = ""
                    page_data[f"Rate {slot_idx}"] = ""
                    page_data[f"Amount (SGD) {slot_idx}"] = ""
                    page_data[f"Amount (EUR) {slot_idx}"] = ""
                    page_data[f"Amount (USD) {slot_idx}"] = ""
                    page_data[f"Amount {slot_idx}"] = ""

            if is_last_page:
                page_data["FooterNote"] = (
                    f"Page {page_idx + 1} of {num_pages} — Final Summary | Sample document for Vessel Call workflow."
                )
            else:
                page_data["FooterNote"] = (
                    f"Page {page_idx + 1} of {num_pages} — Continued on Page {page_idx + 2}..."
                )

            page_schema: List[Dict[str, Any]] = []
            y_shift = 82 if not is_first_page else 0  # Shift table and summary UP on continuation pages

            for el in base_schema:
                el_name = el.get("name", "")
                el_copy = dict(el)
                pos = dict(el_copy.get("position", {}))

                if is_first_page:
                    if el_name in summary_names and el_name != "FooterNote":
                        continue
                    page_schema.append(el_copy)
                else:
                    if el_name in metadata_names:
                        continue
                    if not is_last_page and el_name in summary_names and el_name != "FooterNote":
                        continue

                    if el_name == "DocumentTitle":
                        orig_content = el_copy.get("content", "DISBURSEMENT ACCOUNT")
                        el_copy["content"] = f"{orig_content} — CONTINUATION SHEET"
                        el_copy["width"] = 120
                        el_copy["position"] = {"x": 80, "y": 34}
                        el_copy["alignment"] = "right"
                        page_schema.append(el_copy)
                        continue

                    # Apply upward y-shift for table and summary on continuation pages
                    if "y" in pos and pos["y"] >= 130:
                        pos["y"] = max(50, pos["y"] - y_shift)
                        el_copy["position"] = pos

                    page_schema.append(el_copy)

            # On Page 1: Add Carried Forward Block so page 1 is filled and balanced
            if is_first_page:
                carried_amt_str = f"{currency_str} {page_amt_sum:,.2f}" if currency_str else f"{page_amt_sum:,.2f}"
                page_schema.append(
                    {
                        "name": "CarriedForwardLabel",
                        "type": "text",
                        "content": "SUBTOTAL CARRIED FORWARD",
                        "position": {"x": 104, "y": 230},
                        "width": 46,
                        "height": 9,
                        "fontSize": 7.5,
                        "lineHeight": 1.15,
                        "fontColor": "#0B3557",
                        "backgroundColor": "#EEF3F7",
                        "alignment": "left",
                        "verticalAlignment": "middle",
                        "readOnly": True,
                        "fontName": "Helvetica-Bold",
                    }
                )
                page_schema.append(
                    {
                        "name": "CarriedForwardValue",
                        "type": "text",
                        "content": carried_amt_str,
                        "position": {"x": 150, "y": 230},
                        "width": 48,
                        "height": 9,
                        "fontSize": 8.5,
                        "lineHeight": 1.15,
                        "fontColor": "#111827",
                        "backgroundColor": "#FFFFFF",
                        "alignment": "right",
                        "verticalAlignment": "middle",
                        "readOnly": True,
                        "fontName": "Helvetica-Bold",
                    }
                )
                page_schema.append(
                    {
                        "name": "ContinuationNotice",
                        "type": "text",
                        "content": f"Document continues on Page 2 for remaining items, final tax calculation, and approval remarks.",
                        "position": {"x": 6, "y": 244},
                        "width": 192,
                        "height": 7,
                        "fontSize": 7.5,
                        "lineHeight": 1.15,
                        "fontColor": "#64748B",
                        "alignment": "center",
                        "verticalAlignment": "middle",
                        "readOnly": True,
                        "fontName": "Helvetica",
                    }
                )

            # On continuation pages: Add compact top reference bar
            if not is_first_page:
                vessel_val = str(
                    enriched_data.get("Vessel Name", enriched_data.get("vessel_name", ""))
                )
                doc_no_val = str(
                    enriched_data.get(
                        "Document No.",
                        enriched_data.get(
                            "call_number", enriched_data.get("document_no", "")
                        ),
                    )
                )
                date_val = str(
                    enriched_data.get(
                        "Document Date", enriched_data.get("document_date", "")
                    )
                )
                port_val = str(
                    enriched_data.get("Port of Call", enriched_data.get("port_name", ""))
                )

                mini_header_content = f"Vessel: {vessel_val}   |   Document No: {doc_no_val}   |   Date: {date_val}   |   Port: {port_val}"
                page_schema.append(
                    {
                        "name": "ContinuationRefHeader",
                        "type": "text",
                        "content": mini_header_content,
                        "position": {"x": 6, "y": 44},
                        "width": 192,
                        "height": 7,
                        "fontSize": 8.5,
                        "lineHeight": 1.15,
                        "fontColor": "#0B3557",
                        "backgroundColor": "#EEF3F7",
                        "alignment": "center",
                        "verticalAlignment": "middle",
                        "readOnly": True,
                        "fontName": "Helvetica-Bold",
                    }
                )

            render_schema_page(
                c,
                page_schema,
                page_data,
                page_w_pts,
                page_h_pts,
                unit,
                base_padding,
            )
    else:
        for page_idx, schema_page in enumerate(schemas):
            if page_idx > 0:
                c.showPage()
            render_schema_page(
                c,
                schema_page,
                enriched_data,
                page_w_pts,
                page_h_pts,
                unit,
                base_padding,
            )

    c.save()

    with open(output_path, "rb") as f:
        pdf_bytes = f.read()

    page_count = len(schemas)
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_count = len(doc)
    except Exception:
        pass

    return output_path, pdf_bytes, page_count
