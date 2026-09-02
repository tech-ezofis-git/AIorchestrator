"""Tests for the new PDA template format (table schema, auto-scaling, pagination, and API endpoints)."""
import json
import os
import base64
import fitz
import pytest

from app.pdf_skills.pdf_generator import generate_pdf_from_json, PdfGenerationResult
from app.pdf_skills.template_renderer import (
    render_pdfme_template_to_pdf,
    is_pdfme_template,
    resolve_table_rows,
    auto_flatten_and_enrich_data,
)


@pytest.fixture
def table_schema():
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures", "sample_table_schema.json"))
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_table_schema_structure(table_schema):
    assert is_pdfme_template(table_schema)
    schemas = table_schema.get("schemas", [])
    assert len(schemas) == 1
    assert len(schemas[0]) == 53

    # Verify table element
    table_elem = next((el for el in schemas[0] if el.get("type") == "table"), None)
    assert table_elem is not None
    assert table_elem["name"] == "Cost Details"
    assert table_elem["dataKey"] == "Cost Details"
    assert len(table_elem["head"]) == 9
    assert "Cost Head" in table_elem["head"]
    assert "Tax Amount" in table_elem["head"]


def test_table_schema_render_single_page_2d_array(table_schema, tmp_path):
    data = {
        "Customer": "Oceanic Bulk Carriers Inc.",
        "Job No": "JOB-2026-009",
        "Shipper": "Global Minerals Trading Pte Ltd",
        "Agency Appointment Date": "2026-08-25",
        "Vessel": "MV PACIFIC HORIZON",
        "Currency": "USD",
        "IMO Number": "9876543",
        "Payment Terms": "100% in advance prior to vessel arrival",
        "Voyage Number": "V2608-NORTH",
        "Cost Verified": "Yes",
        "Port": "Singapore (SGSIN)",
        "Related PDA/FDA": "PDA-SG-2026-0881",
        "Terminal": "Jurong Port Berth 5",
        "ETA": "2026-09-02 06:00",
        "ETD": "2026-09-05 18:00",
        "Cargo Type": "Bulk Coal (Steam Coal)",
        "Cargo Quantity": "65,000 MT",
        "Cargo Description": "Steam Coal in bulk, non-hazardous, discharging at Jurong Port",
        "Estimated Subtotal": "21,100.00",
        "Estimated Tax (7.45%)": "1,571.95",
        "TOTAL PDA": "22,671.95",
        "Remarks": "Advance payment required 3 business days prior to ETA.",
        "Cost Details": [
            ["Port Dues", "PSA Singapore", "Port dues", "1", "4,500.00", "4,500.00", "7.45", "335.25", "4,835.25"],
            ["Agency Fee", "Oceanlink Marine", "Agency fee", "1", "3,500.00", "3,500.00", "7.45", "260.75", "3,760.75"]
        ]
    }

    out_file = str(tmp_path / "test_table_single.pdf")
    out_path, pdf_bytes, page_count = render_pdfme_template_to_pdf(table_schema, data, out_file, title="Table Single Page")

    assert page_count == 1
    assert len(pdf_bytes) > 1000
    assert os.path.exists(out_path)

    doc = fitz.open(out_file)
    assert len(doc) == 1
    text = doc[0].get_text()
    assert "MV PACIFIC HORIZON" in text
    assert "Oceanic Bulk Carriers Inc." in text
    assert "JOB-2026-009" in text
    assert "4,835.25" in text
    assert "TOTAL PDA" in text
    assert "22,671.95" in text


def test_table_schema_render_dict_items_auto_calculation(table_schema, tmp_path):
    data = {
        "Customer": "Trans-Asia Shipping",
        "Job No": "JOB-9901",
        "Vessel": "MV ASIA LEADER",
        "Port": "Singapore",
        "items": [
            {
                "cost_head": "Port Dues",
                "vendor": "PSA",
                "cost_description": "Harbour dues",
                "quantity": 1,
                "rate": 5000.0,
                "tax_percent": 7.45
            },
            {
                "cost_head": "Pilotage",
                "vendor": "PSA Marine",
                "cost_description": "Pilotage services",
                "quantity": 2,
                "rate": 1000.0,
                "tax_percent": 7.45
            }
        ]
    }

    out_file = str(tmp_path / "test_table_dict.pdf")
    out_path, pdf_bytes, page_count = render_pdfme_template_to_pdf(table_schema, data, out_file)

    assert page_count == 1
    doc = fitz.open(out_file)
    text = doc[0].get_text()
    assert "MV ASIA LEADER" in text
    assert "Trans-Asia Shipping" in text
    assert "7,000.00" in text  # Subtotal: 5000 + 2000
    assert "521.50" in text    # Tax: 7000 * 0.0745
    assert "7,521.50" in text  # Total


def test_table_schema_multi_page_continuation(table_schema, tmp_path):
    items = []
    for i in range(1, 15):
        items.append({
            "cost_head": f"Service Head {i}",
            "vendor": f"Vendor {i}",
            "cost_description": f"Cost description for service line item #{i}",
            "quantity": 1,
            "rate": 1000.0 * i,
            "amount": 1000.0 * i,
            "tax_percent": 7.45,
            "tax_amount": 74.5 * i,
            "total": 1074.5 * i,
        })

    data = {
        "Customer": "Global Shipping Co",
        "Job No": "MULTI-2026-01",
        "Vessel": "MV MULTI CARRIER",
        "Port": "Singapore",
        "Cost Details": items,
        "Currency": "USD"
    }

    out_file = str(tmp_path / "test_table_multi.pdf")
    out_path, pdf_bytes, page_count = render_pdfme_template_to_pdf(table_schema, data, out_file)

    assert page_count == 2
    doc = fitz.open(out_file)
    assert len(doc) == 2

    # Page 1 checks
    p1_text = doc[0].get_text()
    assert "MV MULTI CARRIER" in p1_text
    assert "SUBTOTAL CARRIED FORWARD" in p1_text
    assert "Page 2" in p1_text

    # Page 2 checks
    p2_text = doc[1].get_text()
    assert "CONTINUATION SHEET" in p2_text
    assert "Vessel: MV MULTI CARRIER" in p2_text


def test_generate_pdf_from_json_with_table_schema(table_schema):
    data = {
        "Customer": "Acme Maritime Ltd",
        "Job No": "JOB-12345",
        "Vessel": "MV ATLANTIC STAR",
        "Port": "Singapore",
        "items": [
            {"service": "Berth Hire", "vendor": "PSA", "rate": 4000.0, "qty": 1, "amount": 4000.0}
        ]
    }

    res = generate_pdf_from_json(data, template_json=table_schema)
    assert isinstance(res, PdfGenerationResult)
    assert res.status == "success"
    assert res.page_count >= 1
    raw = base64.b64decode(res.pdf_base64)
    assert raw.startswith(b"%PDF")


def test_api_pdf_generate_with_table_schema(client, table_schema):
    payload = {
        "templateJson": table_schema,
        "formData": {
            "Customer": "Pacific Lines Inc",
            "Job No": "JOB-2026-081",
            "Vessel": "MV PACIFIC SKY",
            "Port": "Singapore",
            "Currency": "USD",
            "Cost Details": [
                ["Port Dues", "PSA", "Harbour dues", "1", "3,500.00", "3,500.00", "7.45", "260.75", "3,760.75"],
                ["Mooring", "Jurong", "Mooring assist", "2", "400.00", "800.00", "7.45", "59.60", "859.60"]
            ]
        },
        "fileName": "Table-Schema-Result.pdf"
    }

    resp = client.post("/api/pdf/generate", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == "Table-Schema-Result.pdf"
    assert body["page_count"] == 1
    assert len(body["pdf_base64"]) > 0

    # Test download
    dl = client.get(f"/api/pdf/download/{body['filename']}")
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF")
