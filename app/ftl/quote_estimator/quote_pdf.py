"""
Renders the structured quote into downloadable PDFs with reportlab (platypus).

Supports two template formats:
  1. "inflow" (Default, Customer-Facing): Authentic FTL inFlow Sales Estimate layout matching
     the official FTL Distribution PDF estimates (official logo, clean 5-column table, FTL Executive,
     signature dark navy total box, and standard warranty/delivery terms). Free of internal review flags.
  2. "internal" (Engineering Review): Comprehensive review layout showing item review notes,
     "⚠ needs engineering review" warning flags, Remarks box, and yellow Internal Review Notes.

`compute_totals` (imported from quote_template) is the single source of truth for all math.
"""

from __future__ import annotations

import io
import os
from datetime import date
from typing import Any, Dict

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:
    from app.ftl.quote_estimator.quote_template import _money, compute_totals
except ImportError:
    from quote_template import _money, compute_totals

NAVY = colors.HexColor("#1f3a5f")
INFLOW_NAVY = colors.HexColor("#13253b")
INFLOW_TOTAL_NAVY = colors.HexColor("#091626")
DARK_BLUE = colors.HexColor("#0b2545")
LIGHT_GREY = colors.HexColor("#e2e2e2")
FAINT_DIVIDER = colors.HexColor("#ececec")
BODY_GREY = colors.HexColor("#333333")
MUTED_GREY = colors.HexColor("#666666")
FAINT_GREY = colors.HexColor("#999999")
WARN_RED = colors.HexColor("#b30000")
NOTES_BG = colors.HexColor("#fff8e1")
NOTES_BORDER = colors.HexColor("#f0d98c")
NOTES_TEXT = colors.HexColor("#5c4600")
REMARKS_BG = colors.HexColor("#f4f6f9")
REMARKS_BORDER = colors.HexColor("#dde3ec")

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "ftl_logo.png")

_styles = getSampleStyleSheet()

# Styles for inFlow Customer-Facing Template
_inflow_company = ParagraphStyle("inflow_company", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#222222"), leading=12)
_inflow_title = ParagraphStyle("inflow_title", parent=_styles["Normal"], fontName="Helvetica", fontSize=18, textColor=colors.HexColor("#111111"), alignment=TA_RIGHT, leading=20)
_inflow_meta = ParagraphStyle("inflow_meta", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#222222"), alignment=TA_RIGHT, leading=13)
_inflow_label = ParagraphStyle("inflow_label", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.black, leading=12)
_inflow_val = ParagraphStyle("inflow_val", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=BODY_GREY, leading=12)

_inflow_th = ParagraphStyle("inflow_th", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.black, leading=12)
_inflow_th_center = ParagraphStyle("inflow_th_center", parent=_inflow_th, alignment=TA_CENTER)
_inflow_th_right = ParagraphStyle("inflow_th_right", parent=_inflow_th, alignment=TA_RIGHT)

_inflow_td = ParagraphStyle("inflow_td", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=BODY_GREY, leading=12)
_inflow_td_bold = ParagraphStyle("inflow_td_bold", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.black, leading=12)
_inflow_td_center = ParagraphStyle("inflow_td_center", parent=_inflow_td, alignment=TA_CENTER)
_inflow_td_right = ParagraphStyle("inflow_td_right", parent=_inflow_td, alignment=TA_RIGHT)

_inflow_terms = ParagraphStyle("inflow_terms", parent=_styles["Normal"], fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#222222"), leading=11)
_inflow_totals_label = ParagraphStyle("inflow_tot_lbl", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=colors.white, leading=12)
_inflow_totals_val = ParagraphStyle("inflow_tot_val", parent=_styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=colors.white, alignment=TA_RIGHT, leading=12)
_inflow_total_head = ParagraphStyle("inflow_tot_head", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=10.5, textColor=colors.white, leading=14)
_inflow_total_amount = ParagraphStyle("inflow_tot_amt", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=10.5, textColor=colors.white, alignment=TA_RIGHT, leading=14)

# Styles for Internal Review Template
_internal_company_name = ParagraphStyle("int_comp_name", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=14, textColor=NAVY, leading=17)
_internal_company_meta = ParagraphStyle("int_comp_meta", parent=_styles["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED_GREY, leading=11)
_internal_company_tag = ParagraphStyle("int_comp_tag", parent=_styles["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=NAVY, leading=11, spaceBefore=2)
_internal_doc_title = ParagraphStyle("int_doc_title", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, alignment=TA_RIGHT, leading=16)
_internal_doc_meta = ParagraphStyle("int_doc_meta", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, alignment=TA_RIGHT, leading=13)
_internal_label = ParagraphStyle("int_label", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, leading=12)
_internal_value = ParagraphStyle("int_value", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, textColor=BODY_GREY, leading=12)
_internal_th = ParagraphStyle("int_th", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=colors.white, leading=12)
_internal_td = ParagraphStyle("int_td", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, textColor=BODY_GREY, leading=12)
_internal_td_bold = ParagraphStyle("int_td_bold", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=BODY_GREY, leading=12)
_internal_td_center = ParagraphStyle("int_td_center", parent=_internal_td, alignment=TA_CENTER)
_internal_td_right = ParagraphStyle("int_td_right", parent=_internal_td, alignment=TA_RIGHT)
_internal_td_right_bold = ParagraphStyle("int_td_right_bold", parent=_internal_td_bold, alignment=TA_RIGHT)
_internal_note = ParagraphStyle("int_note", parent=_styles["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED_GREY, leading=10)
_internal_flag = ParagraphStyle("int_flag", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=7.5, textColor=WARN_RED, leading=10)
_internal_boilerplate = ParagraphStyle("int_boiler", parent=_styles["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED_GREY, leading=11)
_internal_totals_label = ParagraphStyle("int_tot_lbl", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#555555"), leading=13)
_internal_totals_val = ParagraphStyle("int_tot_val", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, alignment=TA_RIGHT, leading=13)
_internal_totals_total_lbl = ParagraphStyle("int_tot_tot_lbl", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=10.5, textColor=NAVY, leading=15)
_internal_totals_total_val = ParagraphStyle("int_tot_tot_val", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=11.5, textColor=NAVY, alignment=TA_RIGHT, leading=15)
_internal_remarks_head = ParagraphStyle("int_rem_hd", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9.5, textColor=NAVY, leading=13)
_internal_remarks_body = ParagraphStyle("int_rem_bd", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, textColor=BODY_GREY, leading=13)
_internal_notes_head = ParagraphStyle("int_not_hd", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9.5, textColor=colors.HexColor("#7a5c00"), leading=13)
_internal_notes_body = ParagraphStyle("int_not_bd", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, textColor=NOTES_TEXT, leading=13, bulletIndent=0, leftIndent=12)


def _esc(text: str) -> str:
    """Escapes a plain-text value for safe interpolation into reportlab's mini-markup."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(text).replace("\n", "<br/>"), style)


# ---------------------------------------------------------------------------
# Template 1: Official inFlow Sales Estimate PDF (Customer-Facing)
# ---------------------------------------------------------------------------


def generate_inflow_pdf(quote: Dict[str, Any], estimate_number: str, date_str: str | None = None) -> bytes:
    """Generates a clean, customer-facing PDF styled after the official FTL inFlow Sales Estimate."""
    totals = compute_totals(quote)
    d = date.today()
    date_str = date_str or f"{d.strftime('%b')} {d.day}, {d.year}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        title=f"Sales Estimate - {estimate_number}",
    )
    page_width = letter[0] - doc.leftMargin - doc.rightMargin
    elements = []

    # --- Header: Logo + Address (Left) / Sales Estimate & Date (Right) ---
    logo_elem = None
    if os.path.exists(_LOGO_PATH):
        try:
            # 1.6 in width, ~0.75 in height (preserves 2.13 ratio)
            logo_elem = Image(_LOGO_PATH, width=1.6 * inch, height=0.75 * inch)
        except Exception:
            logo_elem = Paragraph("<b>FTL DISTRIBUTION</b>", _inflow_company)
    else:
        logo_elem = Paragraph("<b>FTL DISTRIBUTION</b>", _inflow_company)

    company_text = (
        "<b>FTL Distribution Inc.</b><br/>"
        "8-12555 Coleraine Drive<br/>"
        "Bolton, Ontario<br/>"
        "L7E 0P6<br/>"
        "866-702-1184<br/>"
        "ftl-distribution.ca"
    )
    company_cell = Paragraph(company_text, _inflow_company)

    right_header_text = (
        f'<font size=16><b>Sales Estimate</b></font><br/><br/>'
        f'<b>Estimate No.</b>&nbsp;&nbsp;&nbsp;&nbsp;{_esc(estimate_number)}<br/>'
        f'<b>Date</b>&nbsp;&nbsp;&nbsp;&nbsp;{_esc(date_str)}'
    )
    right_cell = Paragraph(right_header_text, _inflow_meta)

    header_table = Table([[logo_elem, company_cell, right_cell]], colWidths=[1.7 * inch, 2.2 * inch, page_width - 3.9 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 10))

    # --- 4-Column Address and Contact Block ---
    def _addr_col(label: str, lines: list[str]) -> list[Paragraph]:
        res = [Paragraph(f"<b>{_esc(label)}</b>", _inflow_label)]
        for line in lines:
            if line:
                res.append(_p(line, _inflow_val))
        return res

    billing_lines = [totals["customer_name"]] + [l.strip() for l in totals["billing_address"].split("\n") if l.strip()]
    shipping_lines = [totals["customer_name"]] + [l.strip() for l in totals["shipping_address"].split("\n") if l.strip()]
    contact_lines = [totals["contact_name"]] + ([totals["contact_phone"]] if totals["contact_phone"] else [])
    exec_lines = [totals["bdm"]] if totals["bdm"] else []

    addr_table = Table(
        [
            [
                _addr_col("Billing Address", billing_lines),
                _addr_col("Shipping Address", shipping_lines),
                _addr_col("Contact", contact_lines),
                _addr_col("FTL Executive", exec_lines),
            ]
        ],
        colWidths=[page_width * 0.28, page_width * 0.28, page_width * 0.22, page_width * 0.22],
    )
    addr_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
            ]
        )
    )
    elements.append(addr_table)
    elements.append(Spacer(1, 10))

    # --- Line Items Table ---
    headers = [
        Paragraph("Product", _inflow_th),
        Paragraph("Description", _inflow_th),
        Paragraph("Quantity", _inflow_th_center),
        Paragraph("Unit Price", _inflow_th_right),
        Paragraph("Subtotal", _inflow_th_right),
    ]
    rows = [headers]

    for item in totals["line_items"]:
        code_cell = [_p(item["product_code"], _inflow_td_bold)]
        desc_cell = [_p(item["description"], _inflow_td)]
        qty_p = Paragraph(f"{item['qty']:g}", _inflow_td_center)
        price_p = Paragraph(_money(item["unit_price"]), _inflow_td_right)
        subtotal_p = Paragraph(_money(item["subtotal"]), _inflow_td_right)
        rows.append([code_cell, desc_cell, qty_p, price_p, subtotal_p])

    if len(rows) == 1:
        rows.append([Paragraph("No line items.", _inflow_td_center)] + [""] * 4)

    col_widths = [0.32 * page_width, 0.36 * page_width, 0.10 * page_width, 0.11 * page_width, 0.11 * page_width]
    items_table = Table(rows, colWidths=col_widths, repeatRows=1)
    tstyle = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, FAINT_DIVIDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    if len(totals["line_items"]) == 0:
        tstyle.append(("SPAN", (0, 1), (-1, 1)))
    items_table.setStyle(TableStyle(tstyle))
    elements.append(items_table)
    elements.append(Spacer(1, 18))

    # --- Bottom: Legal Boilerplate (Left) / Navy Totals Box (Right) ---
    boiler_text = (
        "<b>Delivery:</b> Lead times confirmed at order.<br/>"
        "<b>Exclusions:</b> Final specs/quantities to be confirmed prior to order and are Final.<br/>"
        "<b>Warranty:</b> Manufacturer warranties only. No additional implied warranties. "
        "Limited to replacement or refund of defective supplied materials.<br/><br/>"
        f"<b>{_esc(estimate_number)}</b>"
    )
    boiler_cell = Paragraph(boiler_text, _inflow_terms)

    # InFlow Navy Totals Box
    totals_data = []
    totals_data.append([Paragraph("Subtotal", _inflow_totals_label), Paragraph(_money(totals["subtotal"]), _inflow_totals_val)])
    if totals["freight_estimate"] > 0:
        totals_data.append([Paragraph("Freight", _inflow_totals_label), Paragraph(_money(totals["freight_estimate"]), _inflow_totals_val)])
    totals_data.append([Paragraph("HST", _inflow_totals_label), Paragraph(_money(totals["hst"]), _inflow_totals_val)])
    totals_data.append([Paragraph("Total", _inflow_total_head), Paragraph(_money(totals["total"]), _inflow_total_amount)])

    totals_box_width = 2.4 * inch
    totals_inner = Table(totals_data, colWidths=[totals_box_width * 0.45, totals_box_width * 0.55])
    
    # Style: all upper rows have INFLOW_NAVY background, last row has darker INFLOW_TOTAL_NAVY
    num_rows = len(totals_data)
    box_styles = [
        ("BACKGROUND", (0, 0), (-1, num_rows - 2), INFLOW_NAVY),
        ("BACKGROUND", (0, num_rows - 1), (-1, num_rows - 1), INFLOW_TOTAL_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -2), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 4),
        ("TOPPADDING", (0, num_rows - 1), (-1, num_rows - 1), 7),
        ("BOTTOMPADDING", (0, num_rows - 1), (-1, num_rows - 1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]
    totals_inner.setStyle(TableStyle(box_styles))

    bottom_table = Table([[boiler_cell, totals_inner]], colWidths=[page_width - totals_box_width - 0.2 * inch, totals_box_width])
    bottom_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(bottom_table)

    doc.build(elements)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Template 2: Internal Engineering Review PDF
# ---------------------------------------------------------------------------


def generate_internal_pdf(quote: Dict[str, Any], estimate_number: str, date_str: str | None = None) -> bytes:
    """Generates the comprehensive Internal Review PDF with review notes, warning flags, and assumptions."""
    totals = compute_totals(quote)
    d = date.today()
    date_str = date_str or f"{d.strftime('%b')} {d.day}, {d.year}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        title=f"Internal Review - {estimate_number}",
    )
    page_width = letter[0] - doc.leftMargin - doc.rightMargin
    elements = []

    # --- Header: letterhead (left) / Sales Estimate + No./Date (right) ---
    header_left = [
        _p("FTL Distribution Inc.", _internal_company_name),
        _p("info@ftl-distribution.ca", _internal_company_meta),
        _p("8-12555 Coleraine Drive, Bolton, Ontario L7E 0P6", _internal_company_meta),
        _p("Your Lift, Our Parts – Elevate With FTL", _internal_company_tag),
    ]
    header_right = [
        _p("Sales Estimate", _internal_doc_title),
        Paragraph(f"Estimate No. <b>{_esc(estimate_number)}</b>", _internal_doc_meta),
        _p(f"Date {date_str}", _internal_doc_meta),
    ]
    header_table = Table([[header_left, header_right]], colWidths=[page_width * 0.6, page_width * 0.4])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 2, NAVY),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 10))

    # --- Address and Contact Block ---
    def _block(label: str, lines: list[Paragraph]) -> list[Paragraph]:
        return [_p(label, _internal_label)] + lines

    addr_table = Table(
        [
            [
                _block("Billing Address", [_p(totals["customer_name"], _internal_value), _p(totals["billing_address"], _internal_value)]),
                _block("Shipping Address", [_p(totals["customer_name"], _internal_value), _p(totals["shipping_address"], _internal_value)]),
                _block("Contact", [_p(totals["contact_name"], _internal_value), _p(totals["contact_phone"], _internal_value), Spacer(1, 4), _p("FTL BDM", _internal_label), _p(totals["bdm"], _internal_value)]),
                _block("Payment Terms", [_p(totals["payment_terms"] or "—", _internal_value)]),
            ]
        ],
        colWidths=[page_width * 0.27, page_width * 0.27, page_width * 0.23, page_width * 0.23],
    )
    addr_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(addr_table)
    elements.append(Spacer(1, 14))

    # --- Line items table ---
    header_row = [
        Paragraph("#", _internal_th),
        Paragraph("Product", _internal_th),
        Paragraph("Qty", _internal_th),
        Paragraph("Price", _internal_th),
        Paragraph("Subtotal", _internal_th),
    ]
    rows = [header_row]
    for i, item in enumerate(totals["line_items"], start=1):
        desc_cell = [_p(item["product_code"], _internal_td_bold), _p(item["description"], _internal_td)]
        if item["needs_engineering_review"]:
            desc_cell.append(_p("⚠ needs engineering review", _internal_flag))
        if item["note"]:
            desc_cell.append(_p(item["note"], _internal_note))
        rows.append(
            [
                Paragraph(str(i), _internal_td_center),
                desc_cell,
                Paragraph(f"{item['qty']:g}", _internal_td_center),
                Paragraph(_money(item["unit_price"]), _internal_td_right),
                Paragraph(_money(item["subtotal"]), _internal_td_right_bold),
            ]
        )
    if len(rows) == 1:
        rows.append([Paragraph("No line items.", _internal_td_center)] + [""] * 4)

    col_widths = [0.05 * page_width, 0.47 * page_width, 0.1 * page_width, 0.18 * page_width, 0.2 * page_width]
    items_table = Table(rows, colWidths=col_widths, repeatRows=1)
    items_style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if len(totals["line_items"]) == 0:
        items_style.append(("SPAN", (0, 1), (-1, 1)))
        items_style.append(("ALIGN", (0, 1), (0, 1), "CENTER"))
    items_table.setStyle(TableStyle(items_style))
    elements.append(items_table)
    elements.append(Spacer(1, 14))

    # --- Boilerplate (left) + Totals box (right) ---
    boiler_cell = _p(
        "Delivery: Lead times confirmed at order. "
        "Exclusions: Final specs/quantities to be confirmed prior to order and are Final. "
        "Warranty: Manufacturer warranties only. No additional implied warranties. Limited to "
        "replacement or refund of defective supplied materials. Suitability, installation, testing, "
        "adjustment, and regulatory compliance are the responsibility of the licensed elevator "
        "contractor.",
        _internal_boilerplate,
    )

    freight_label = "Freight"
    if totals["freight_note"]:
        freight_label = f"Freight<br/><font size=6.5 color='#777777'>{_esc(totals['freight_note'])}</font>"

    totals_rows = [
        [Paragraph("Subtotal", _internal_totals_label), Paragraph(_money(totals["subtotal"]), _internal_totals_val)],
        [Paragraph(freight_label, _internal_totals_label), Paragraph(_money(totals["freight_estimate"]), _internal_totals_val)],
        [Paragraph("HST (13%)", _internal_totals_label), Paragraph(_money(totals["hst"]), _internal_totals_val)],
        [Paragraph("Total", _internal_totals_total_lbl), Paragraph(_money(totals["total"]), _internal_totals_total_val)],
    ]
    totals_box = Table(totals_rows, colWidths=[page_width * 0.42 * 0.55, page_width * 0.42 * 0.45])
    totals_box.setStyle(
        TableStyle(
            [
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, NAVY),
                ("TOPPADDING", (0, -1), (-1, -1), 7),
            ]
        )
    )

    footer_table = Table([[boiler_cell, totals_box]], colWidths=[page_width * 0.58, page_width * 0.42])
    footer_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 16),
            ]
        )
    )
    elements.append(footer_table)
    elements.append(Spacer(1, 14))

    # --- Remarks box ---
    remarks_text = totals["project_name"]
    if totals["project_name"] and totals["remarks"]:
        remarks_text += "\n" + totals["remarks"]
    elif totals["remarks"]:
        remarks_text = totals["remarks"]

    remarks_inner = Table(
        [[_p("Remarks", _internal_remarks_head)], [_p(remarks_text, _internal_remarks_body)]],
        colWidths=[page_width],
    )
    remarks_inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), REMARKS_BG),
                ("BOX", (0, 0), (-1, -1), 0.75, REMARKS_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
            ]
        )
    )
    elements.append(remarks_inner)

    # --- Internal review notes (assumptions) ---
    if totals["assumptions"]:
        elements.append(Spacer(1, 10))
        bullet_lines = "<br/>".join(f"&bull; {_esc(a)}" for a in totals["assumptions"])
        notes_inner = Table(
            [
                [_p("Internal review notes (not part of the customer-facing estimate)", _internal_notes_head)],
                [Paragraph(bullet_lines, _internal_notes_body)],
            ],
            colWidths=[page_width],
        )
        notes_inner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), NOTES_BG),
                    ("BOX", (0, 0), (-1, -1), 0.75, NOTES_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("TOPPADDING", (0, 1), (-1, 1), 0),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
                ]
            )
        )
        elements.append(notes_inner)

    doc.build(elements)
    return buf.getvalue()


def generate_quote_pdf(
    quote: Dict[str, Any], estimate_number: str, date_str: str | None = None, template_type: str = "inflow"
) -> bytes:
    """Generates quote PDF with the chosen template ('inflow' or 'internal'/'internal_review'). Defaults to 'inflow'."""
    norm = str(template_type or "inflow").strip().lower()
    if norm in ("internal", "internal_review"):
        return generate_internal_pdf(quote, estimate_number, date_str)
    return generate_inflow_pdf(quote, estimate_number, date_str)
