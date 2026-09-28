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


def test_rejects_non_template():
    with pytest.raises(ValueError):
        render_template_pdf({"foo": 1}, {})


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
