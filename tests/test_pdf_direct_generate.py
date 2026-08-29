"""POST /api/pdf/generate — templateJson + formData contract."""

MINI_TEMPLATE = {
    "schemas": [
        [
            {
                "name": "CustomerValue",
                "type": "text",
                "dataKey": "Customer / Principal",
                "position": {"x": 10, "y": 20},
                "width": 80,
                "height": 8,
                "fontSize": 10,
                "fontName": "Helvetica",
            },
            {
                "name": "DocumentNoValue",
                "type": "text",
                "dataKey": "Document No.",
                "position": {"x": 10, "y": 35},
                "width": 80,
                "height": 8,
                "fontSize": 10,
                "fontName": "Helvetica",
            },
        ]
    ],
    "basePdf": {"width": 210, "height": 297, "padding": [20, 10, 20, 10]},
}


def test_direct_pdf_generate_template_json_form_data(client):
    response = client.post(
        "/api/pdf/generate",
        json={
            "templateJson": MINI_TEMPLATE,
            "formData": {
                "Customer / Principal": "ACME Shipping",
                "Document No.": "PDA-2026-001",
                "Service / Cost Item 1": "Port dues",
            },
            "fileName": "PDA-VesselCall-REQ-2026-001.pdf",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["filename"] == "PDA-VesselCall-REQ-2026-001.pdf"
    assert body["page_count"] >= 1
    assert body["pdf_base64"]
    assert "download_url" not in body
    assert "preview_url" not in body
    assert "static_url" not in body

    dl = client.get(f"/api/pdf/download/{body['filename']}")
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF")


def test_normalize_direct_pdf_request_pdf_template_alias():
    from app.pdf_skills.pdf_generator import normalize_direct_pdf_request

    parsed = normalize_direct_pdf_request(
        {
            "pdfTemplate": MINI_TEMPLATE,
            "formData": {"Customer / Principal": "ACME"},
            "fileName": "custom.pdf",
        }
    )
    assert parsed["template_json"] == MINI_TEMPLATE
    assert parsed["data"]["Customer / Principal"] == "ACME"
    assert parsed["output_filename"] == "custom.pdf"
