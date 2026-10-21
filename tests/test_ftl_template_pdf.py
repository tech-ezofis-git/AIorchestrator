"""formData + templateJson (base64 pdfme template) -> pdf_base64 for intent=ftl_quote_estimator."""
import base64
import json

import fitz
import pytest

from app.ftl.quote_estimator.template_pdf import prepare_form_data, render_template_pdf


def _template():
    return {
        "basePdf": {"width": 210, "height": 297, "padding": [0, 0, 0, 0]},
        "schemas": [[
            {"name": "titleLabel", "type": "text", "readOnly": True, "content": "FTL DISTRIBUTION",
             "position": {"x": 10, "y": 10}, "width": 100, "height": 10, "fontSize": 16},
            {"name": "orderNumber", "type": "text", "dataKey": "Order Number", "content": "EST-SAMPLE",
             "position": {"x": 140, "y": 10}, "width": 60, "height": 8, "fontSize": 10},
            {"name": "companyName", "type": "text", "dataKey": "Company Name", "content": "Sample Co",
             "position": {"x": 10, "y": 30}, "width": 90, "height": 8, "fontSize": 10},
            {"name": "shipTo", "type": "text", "dataKey": "Ship To", "content": "SAMPLE SHIP TO",
             "position": {"x": 110, "y": 30}, "width": 90, "height": 8, "fontSize": 10},
            {"name": "lineItems", "type": "table", "dataKey": "Line Item",
             "head": ["Product", "Description", "Qty", "Price", "Subtotal"],
             "headWidthPercentages": [20, 40, 10, 15, 15],
             "position": {"x": 10, "y": 60}, "width": 190, "height": 20, "content": []},
            {"name": "totalLabel", "type": "text", "readOnly": True, "content": "TOTAL",
             "position": {"x": 130, "y": 250}, "width": 30, "height": 8},
            {"name": "total", "type": "text", "dataKey": "Total",
             "position": {"x": 160, "y": 250}, "width": 40, "height": 8},
            {"name": "footerNote", "type": "text", "readOnly": True, "content": "Thank you for your business",
             "position": {"x": 10, "y": 286}, "width": 120, "height": 5, "fontSize": 8},
        ]],
    }


def _b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _items(n):
    return [
        {"Product": f"SKU-{i}", "Description": f"Part number {i}", "Qty": 1, "Price": 10.0, "Subtotal": 10.0}
        for i in range(n)
    ]


def _pages_text(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def _span_color(page, needle: str) -> int:
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if needle in span.get("text", ""):
                    return int(span.get("color") or 0)
    raise AssertionError(f"missing {needle!r}")


def test_fills_fields_and_table():
    form = {"Order Number": "EST-900061", "Company Name": "ATTA Elevators", "Line Item": _items(2), "Total": 20.0}
    pdf, pages = render_template_pdf(_template(), form)
    assert pages == 1
    text = _pages_text(pdf)[0]
    for expected in ("EST-900061", "ATTA Elevators", "SKU-0", "SKU-1", "20.00", "FTL DISTRIBUTION"):
        assert expected in text


def test_missing_fields_render_blank_not_sample_content():
    pdf, _ = render_template_pdf(_template(), {"Order Number": "EST-1"})
    text = _pages_text(pdf)[0]
    assert "EST-1" in text
    assert "Sample Co" not in text and "SAMPLE SHIP TO" not in text and "EST-SAMPLE" not in text


def test_internal_keys_are_mapped_to_template_names():
    form = {"estimate_number": "EST-7", "customer_name": "Snake Co",
            "line_items": [{"product_code": "P-1", "quantity": 3, "unit_price": 4.5}]}
    assert prepare_form_data(form)["Company Name"] == "Snake Co"
    text = _pages_text(render_template_pdf(_template(), form)[0])[0]
    assert "EST-7" in text and "Snake Co" in text and "P-1" in text


def test_many_line_items_paginate_with_header_and_totals_on_last_page():
    form = {"Order Number": "EST-BIG", "Line Item": _items(60), "Total": 600.0}
    pdf, pages = render_template_pdf(_template(), form)
    assert pages > 1
    texts = _pages_text(pdf)
    joined = "\n".join(texts)
    for i in range(60):
        assert f"SKU-{i}\n" in joined or f"SKU-{i} " in joined
    for idx, text in enumerate(texts, start=1):
        assert "EST-BIG" in text
        assert "Thank you for your business" in text
        assert f"Page {idx} of {pages}" in text
        assert "SUBTOTAL CARRIED FORWARD" not in text and "Vessel" not in text
    assert "600.00" in texts[-1]
    assert all("600.00" not in t for t in texts[:-1])


def _logo_data_url():
    with open("app/ftl/quote_estimator/assets/ftl_logo.png", "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _branded_template(logo_content):
    return {
        "basePdf": {"width": 210, "height": 297, "padding": [15, 15, 15, 15]},
        "schemas": [[
            {"name": "CompanyLogo", "type": "image", "dataKey": "company_logo", "content": logo_content,
             "position": {"x": 15, "y": 15}, "width": 42, "height": 20},
            {"name": "StandardTermsBlock", "type": "text",
             "content": "Delivery: Lead times confirmed at order.\n\n{{estimate_number}}",
             "position": {"x": 15, "y": 235}, "width": 115, "height": 30, "fontSize": 8},
            {"name": "TotalsContainer", "type": "container", "position": {"x": 135, "y": 235},
             "width": 60, "height": 42, "backgroundColor": "#13253B",
             "children": [
                 {"name": "SubtotalRow", "type": "key_value", "label": "Subtotal", "dataKey": "subtotal",
                  "textColor": "#FFFFFF"},
                 {"name": "FreightRow", "type": "key_value", "label": "Freight", "dataKey": "freight",
                  "textColor": "#FFFFFF", "hideIfZero": True},
                 {"name": "TotalRow", "type": "key_value", "label": "Total", "dataKey": "total",
                  "backgroundColor": "#091626", "textColor": "#FFFFFF", "fontName": "Helvetica-Bold"},
             ]},
        ]],
    }


@pytest.mark.parametrize("raw_base64", [False, True], ids=["data_url", "raw_base64"])
def test_embedded_logo_renders_without_form_data_value(raw_base64):
    logo = _logo_data_url()
    if raw_base64:
        logo = logo.split(",", 1)[1]
    pdf, _ = render_template_pdf(_branded_template(logo), {"estimate_number": "EST-1"})
    assert pdf.count(b"/Subtype /Image") == 1


def test_container_rows_and_static_placeholders():
    form = {"Order Number": "EST-42", "subtotal": "$3,450.00", "freight": "$0.00", "total": "$3,898.50"}
    text = _pages_text(render_template_pdf(_branded_template(""), form)[0])[0]
    for expected in ("Subtotal", "$3,450.00", "Total", "$3,898.50", "EST-42"):
        assert expected in text
    assert "Freight" not in text
    assert "{{" not in text


def test_unfilled_static_placeholder_is_dropped():
    text = _pages_text(render_template_pdf(_branded_template(""), {"subtotal": "1.00"})[0])[0]
    assert "{{" not in text and "estimate_number" not in text


def test_rejects_non_template():
    with pytest.raises(ValueError):
        render_template_pdf({"foo": 1}, {})


def test_quote_type_quote_2_fills_prefixed_line_items(client, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("model must not be called for template input")

    monkeypatch.setattr("app.ftl.quote_estimator.agent.run_quote_estimation", fail)
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-q2",
            "intent": "ftl_quote_estimator",
            "payload": {
                "quoteType": "Quote 2",
                "formData": {
                    "Order Number": "EST-900201",
                    "Company Name": "ATTA Elevators",
                    "Line Item": _items(1),
                    "Total": 10.0,
                    "quote_2": {
                        "quote_2 Status": "GENERATED",
                        "quote_2 Order Number": "EST-900201-Q2",
                        "quote_2 Line Item": [
                            {
                                "Product": "FS_VISIONPLUS_3D_CAR_DOOR_DET",
                                "Description": "3D detector",
                                "Qty": 2,
                                "Price": 1295.0,
                                "Subtotal": 2590.0,
                            }
                        ],
                        "quote_2 Total": 2590.0,
                    },
                },
                "templateJson": _template(),
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    pdf = base64.b64decode(body["pdf_base64"])
    text = _pages_text(pdf)[0]
    assert "VISIONPLUS" in text and "3D detector" in text
    assert "SKU-0" not in text
    assert "Quote 2" in body["reply"]
    assert body["pdf_filename"] == "EST-900201-Q2.pdf"


def test_quote_type_quote_2_skipped_when_no_form_values(client, monkeypatch):
    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model")),
    )
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-q2-miss",
            "intent": "ftl_quote_estimator",
            "payload": {
                "quoteType": "Quote 2",
                "formData": {
                    "Order Number": "EST-1",
                    "Line Item": _items(1),
                    "quote_2": {
                        "quote_2 Status": "NO VALID RECOMMENDED VARIANT",
                        "quote_2 Reason": "No approved rule.",
                    },
                },
                "templateJson": _template(),
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert not body.get("pdf_base64")
    assert "skipped" in body["reply"].lower()
    assert not body.get("error")


def test_quote_type_quote_3_skipped_when_line_item_empty(client, monkeypatch):
    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model")),
    )
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-q3-empty",
            "intent": "ftl_quote_estimator",
            "payload": {
                "quoteType": "quote_3",
                "formData": {
                    "Order Number": "EST-900001",
                    "Line Item": _items(1),
                    "Total": "158206.4",
                    "quote_3 Line Item": [],
                    "quote_3 Total": "158206.4",
                },
                "templateJson": _template(),
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert not body.get("pdf_base64")
    assert "skipped" in body["reply"].lower()
    assert not body.get("error")


def test_quote_type_quote_2_from_ui_formdata_without_generated_status(client, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("model must not be called for template input")

    monkeypatch.setattr("app.ftl.quote_estimator.agent.run_quote_estimation", fail)
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-q2-ui",
            "intent": "ftl_quote_estimator",
            "payload": {
                "quoteType": "Quote 2",
                "formData": {
                    "Order Number": "EST-1",
                    "Company Name": "ATTA Elevators",
                    "Line Item": _items(1),
                    "quote_2": {
                        "quote_2 Order Number": "EST-1-MANUAL",
                        "quote_2 Line Item": [
                            {
                                "Product": "FS_VISIONPLUS_3D_CAR_DOOR_DET",
                                "Description": "3D detector",
                                "Qty": 1,
                                "Price": 1295.0,
                                "Subtotal": 1295.0,
                            }
                        ],
                        "quote_2 Total": 1295.0,
                    },
                },
                "templateJson": _template(),
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    pdf = base64.b64decode(body["pdf_base64"])
    text = _pages_text(pdf)[0]
    assert "VISIONPLUS" in text
    assert "EST-1-MANUAL" in text
    assert "SKU-0" not in text
    assert body["pdf_filename"] == "EST-1-MANUAL.pdf"


@pytest.mark.parametrize("encode", [_b64, lambda t: "data:application/json;base64," + _b64(t), lambda t: t])
def test_chat_returns_pdf_base64(client, monkeypatch, encode):
    def fail(*args, **kwargs):
        raise AssertionError("model must not be called for template input")

    monkeypatch.setattr("app.ftl.quote_estimator.agent.run_quote_estimation", fail)
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-1",
            "intent": "ftl_quote_estimator",
            "payload": {
                "formData": {"Order Number": "EST-900061", "Company Name": "ATTA Elevators", "Line Item": _items(3)},
                "templateJson": encode(_template()),
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pdf_filename"] == "EST-900061.pdf"
    pdf = base64.b64decode(body["pdf_base64"])
    assert pdf.startswith(b"%PDF")
    assert "ATTA Elevators" in _pages_text(pdf)[0]


def test_chat_invalid_base64_template_is_422(client):
    res = client.post(
        "/chat",
        json={
            "session_id": "ftl-tpl-2",
            "intent": "ftl_quote_estimator",
            "payload": {"formData": {"Order Number": "X"}, "templateJson": "not-base64!!"},
        },
    )
    assert res.status_code == 422


def test_chat_form_data_without_template_reports_error(client):
    res = client.post(
        "/chat",
        json={"session_id": "ftl-tpl-3", "intent": "ftl_quote_estimator",
              "payload": {"formData": {"Order Number": "X"}}},
    )
    assert res.status_code == 200
    body = res.json()
    assert not body.get("pdf_base64")
    assert "templateJson" in body["reply"]


def test_chat_multipart_template(client):
    res = client.post(
        "/chat",
        files={
            "session_id": (None, "ftl-tpl-4"),
            "intent": (None, "ftl_quote_estimator"),
            "formData": (None, json.dumps({"Order Number": "EST-5", "Line Item": _items(1)})),
            "templateJson": (None, _b64(_template())),
        },
    )
    assert res.status_code == 200, res.text
    assert base64.b64decode(res.json()["pdf_base64"]).startswith(b"%PDF")


def _quote_template():
    return {
        "basePdf": {"width": 210, "height": 297, "padding": [0, 0, 0, 0]},
        "schemas": [[
            {"name": "TopBar", "type": "rectangle", "backgroundColor": "#102C47",
             "position": {"x": 0, "y": 0}, "width": 210, "height": 3},
            {"name": "BillCard", "type": "rectangle", "backgroundColor": "#F1F5F9",
             "position": {"x": 16, "y": 63}, "width": 85, "height": 25},
            {"name": "ShipCard", "type": "rectangle", "backgroundColor": "#F1F5F9",
             "position": {"x": 109, "y": 63}, "width": 85, "height": 25},
            {"name": "BillLabel", "type": "text", "readOnly": True, "content": "BILL TO",
             "fontColor": "#102C47", "fontName": "Helvetica-Bold", "fontSize": 7.5,
             "position": {"x": 20, "y": 66}, "width": 40, "height": 5},
            {"name": "Billing Address", "type": "text", "dataKey": "Billing Address",
             "fontColor": "#26384A", "fontSize": 8,
             "position": {"x": 20, "y": 72}, "width": 76, "height": 14},
            {"name": "Items Table", "type": "table", "dataKey": "Line Item",
             "position": {"x": 16, "y": 104}, "width": 178,
             "head": ["#", "DESCRIPTION / PRODUCT", "QTY", "UNIT PRICE", "AMOUNT"],
             "columns": ["#", "Product", "Qty", "Price", "Subtotal"],
             "headWidthPercentages": [8, 46, 10, 18, 18],
             "headStyles": {"backgroundColor": "#102C47", "textColor": "#FFFFFF", "fontName": "Helvetica-Bold"},
             "bodyStyles": {"backgroundColor": "#FFFFFF", "borderBottom": "0.5px solid #D7E1EB"},
             "multiLineColumns": {
                 "Product": [
                     {"key": "Product", "fontName": "Helvetica-Bold"},
                     {"key": "Description", "hideIfEmpty": True},
                 ]
             }},
            {"name": "Totals Container", "type": "container",
             "position": {"x": 123, "y": 0}, "width": 71, "height": 36,
             "flowAfter": "Items Table", "flowGap": 8,
             "children": [
                 {"name": "Subtotal", "type": "key_value", "label": "Subtotal", "dataKey": "Subtotal",
                  "textColor": "#64748B", "fontSize": 9},
                 {"name": "Total", "type": "key_value", "label": "TOTAL (CAD)", "dataKey": "Total",
                  "fontName": "Helvetica-Bold", "fontSize": 11},
             ]},
            {"name": "Remarks Label", "type": "text", "readOnly": True, "content": "Remarks",
             "fontColor": "#1F3A5F", "fontName": "Helvetica-Bold", "fontSize": 8,
             "position": {"x": 16, "y": 0}, "width": 75, "height": 5,
             "flowAfter": "Totals Container", "flowGap": 10},
            {"name": "Remarks", "type": "text", "dataKey": "Remarks",
             "fontColor": "#333333", "fontSize": 8, "boxed": True, "dynamicHeight": True,
             "boxBackgroundColor": "#F4F6F9", "boxBorderColor": "#DDE3EC", "boxPadding": 4,
             "position": {"x": 16, "y": 0}, "width": 178, "height": 28,
             "flowAfter": "Remarks Label", "flowGap": 2},
            {"name": "Internal Notes Label", "type": "text", "readOnly": True,
             "content": "Internal review notes (not part of the customer-facing estimate)",
             "fontColor": "#7A5C00", "fontName": "Helvetica-Bold", "fontSize": 8,
             "hideIfEmpty": "Internal Notes",
             "position": {"x": 16, "y": 0}, "width": 178, "height": 5,
             "flowAfter": "Remarks", "flowGap": 6},
            {"name": "Internal Notes", "type": "text", "dataKey": "Internal Notes",
             "fontColor": "#5C4600", "fontSize": 8, "boxed": True, "dynamicHeight": True,
             "boxBackgroundColor": "#FFF8E1", "boxBorderColor": "#F0D98C", "boxPadding": 4,
             "hideIfEmpty": True,
             "position": {"x": 16, "y": 0}, "width": 178, "height": 36,
             "flowAfter": "Internal Notes Label", "flowGap": 2},
        ]],
    }


def test_quote_template_keeps_card_and_navy_backgrounds():
    form = {
        "Billing Address": "40 North Rivermede Rd.",
        "Line Item": [
            {"Product": "VISIONPLUS_3D_DETECTOR", "Description": "3D detector", "Qty": 2, "Price": 1295, "Subtotal": 2590},
            {"Product": "SGV2_DOOR_TOOLS", "Description": "Programming tool", "Qty": 1, "Price": 698.5, "Subtotal": 698.5},
        ],
        "Subtotal": 3288.5,
        "Total": 3715.99,
        "Remarks": "Lead times confirmed at order.",
    }
    pdf, pages = render_template_pdf(_quote_template(), form)
    assert pages == 1
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        text = page.get_text()
        fills = []
        for drawing in page.get_drawings():
            fill = drawing.get("fill")
            if fill:
                fills.append(tuple(round(c, 3) for c in fill[:3]))
        assert (0.063, 0.173, 0.278) in fills  # #102C47 navy
        assert (0.945, 0.961, 0.976) in fills  # #F1F5F9 bill/ship cards
        assert (0.973, 0.98, 0.988) in fills  # #F8FAFC zebra row
        assert "VISIONPLUS_3D_DETECTOR" in text
        assert "3D detector" in text
        assert "Remarks" in text
        assert "Lead times confirmed at order." in text
        assert "Internal review notes" not in text
        assert "TOTAL (CAD)" in text
        assert "$2,590.00" in text and "$698.50" in text
        assert "$3,288.50" in text and "$3,715.99" in text
        assert _span_color(page, "DESCRIPTION / PRODUCT") == _span_color(page, "TOTAL (CAD)") == 0xFFFFFF


def test_container_formats_numeric_totals_as_money():
    form = {"subtotal": 3450, "total": 3898.5, "freight": 0}
    text = _pages_text(render_template_pdf(_branded_template(""), form)[0])[0]
    assert "$3,450.00" in text
    assert "$3,898.50" in text
    assert "Freight" not in text


def test_remarks_body_filled_from_notes_and_project():
    form = {
        "Project": "145 Richmond Avenue West",
        "Notes": "Confirm door type, width and hand before release.",
        "Line Item": _items(1),
        "Subtotal": 10,
        "Total": 11.3,
    }
    text = _pages_text(render_template_pdf(_quote_template(), form)[0])[0]
    assert "Remarks" in text
    assert "145 Richmond Avenue West" in text
    assert "Confirm door type, width and hand before release." in text
    assert "$10.00" in text and "$11.30" in text


def test_remarks_and_internal_notes_match_estimator_layout():
    form = {
        "Project": "145 Richmond Avenue West",
        "Remarks": (
            "Quote based on email request for a Wittur 4C-R (SGV2) car door operator modernization. "
            "Attached survey images were not available to extract required details."
        ),
        "Release Status": "HOLD — technical / commercial review required before release",
        "Release Reasons": [
            "Attached files not read — The email attaches 1000011914.jpg, 1000011913.jpg, 1000011877.jpg.",
            "Freight — this is not a single SGV2 package (no door operator) — larger and multi-unit projects follow the manual project-freight review",
            "Line SGV2_DOOR_TOOLS cannot auto-release.",
            "Line SGV2_CAR_DOOR_RESTRICTOR cannot auto-release.",
        ],
        "Line Item": _items(1),
        "Subtotal": 10,
        "Total": 11.3,
    }
    text = _pages_text(render_template_pdf(_quote_template(), form)[0])[0]
    assert "145 Richmond Avenue West" in text
    assert "Wittur 4C-R (SGV2) car door operator modernization" in text
    assert "Internal review notes (not part of the customer-facing estimate)" in text
    assert "Release status: HOLD" in text
    assert "Attached files not read" in text
    assert "SGV2_DOOR_TOOLS cannot auto-release" in text


def test_amount_column_fills_from_subtotal_or_qty_times_price():
    tpl = _quote_template()
    table = next(field for field in tpl["schemas"][0] if field.get("type") == "table")
    table["columns"] = ["#", "Product", "Qty", "Price", "Amount"]
    form = {
        "Line Item": [
            {"Product": "SGV2_DOOR_OP_2C42_R", "Qty": "1.0", "Price": "5730.32"},
            {"Product": "VISIONPLUS", "Qty": 2, "Price": 1295, "Subtotal": 2590},
        ],
        "Subtotal": 8320.32,
        "Total": 9401.96,
    }
    text = _pages_text(render_template_pdf(tpl, form)[0])[0]
    assert "$5,730.32" in text
    assert text.count("$5,730.32") >= 2
    assert "$1,295.00" in text and "$2,590.00" in text
    assert "\n1\n" in text or " 1 " in text or "\n1\n" in text.replace("1.0", "1")


def test_remarks_does_not_repeat_project_or_duplicate_paragraph():
    form = {
        "Project": "145 Richmond Ave W",
        "Remarks": (
            "145 Richmond Avenue West – historical RFQ forward received (attachments not readable here). "
            "Request: 'wittur 4C-R operator'.\n"
            "This estimate cannot be fully resolved without the attached survey details.\n"
            "145 Richmond Avenue West – historical RFQ forward received (attachments not readable here). "
            "Request: 'wittur 4C-R operator'.\n"
            "This estimate cannot be fully resolved without the attached survey details."
        ),
        "Line Item": _items(4),
        "Subtotal": 40,
        "Total": 45.2,
    }
    pdf, pages = render_template_pdf(_quote_template(), form)
    text = _pages_text(pdf)[0]
    assert pages == 1
    assert text.count("historical RFQ forward received") == 1
    assert text.count("145 Richmond") == 1
    assert "Internal review notes" not in text


def test_review_flags_on_line_items_fill_internal_notes():
    form = {
        "Project": "145 Richmond Ave W",
        "Line Item": [
            {"Product": "SGV2_DOOR_TOOLS", "Qty": 1, "Price": 698.5, "Needs Engineering Review": True},
        ],
        "Subtotal": 698.5,
        "Total": 789.31,
    }
    text = _pages_text(render_template_pdf(_quote_template(), form)[0])[0]
    assert "Internal review notes (not part of the customer-facing estimate)" in text
    assert "Line SGV2_DOOR_TOOLS cannot auto-release." in text
    assert "145 Richmond Ave W" in text
