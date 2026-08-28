"""Integration and endpoint tests for PDF generator agent (/chat with intent=pdf and download/preview endpoints)."""
import json
import pytest


def test_chat_pdf_intent_direct_json(client):
    invoice_payload = {
        "invoice_number": "INV-2026-901",
        "vendor": "Acme Global Solutions",
        "customer": "Tech Innovators Inc",
        "date": "2026-08-28",
        "items": [
            {"name": "AI Orchestration Platform Subscription", "quantity": 1, "rate": 3500.0, "amount": 3500.0},
            {"name": "Premium Technical Support", "quantity": 1, "rate": 500.0, "amount": 500.0}
        ],
        "total_amount": 4000.0,
        "currency": "USD"
    }

    response = client.post(
        "/chat",
        json={
            "session_id": "session-pdf-1",
            "intent": "pdf",
            "payload": {
                "pdf_title": "Tax Invoice INV-2026-901",
                "pdf_theme": "corporate_blue",
                "pdf_json": invoice_payload
            }
        }
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == "session-pdf-1"
    assert "pdf_result" in body and body["pdf_result"] is not None
    pdf_res = body["pdf_result"]
    assert pdf_res["status"] == "success"
    assert pdf_res["page_count"] >= 1
    assert pdf_res["title"] == "Tax Invoice INV-2026-901"
    assert pdf_res["download_url"].startswith("/api/pdf/download/")
    assert pdf_res["preview_url"].startswith("/api/pdf/preview/")
    assert "pdf_base64" in pdf_res and len(pdf_res["pdf_base64"]) > 0

    filename = pdf_res["filename"]

    # Test Download endpoint
    dl_resp = client.get(f"/api/pdf/download/{filename}")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"] == "application/pdf"
    assert f'filename="{filename}"' in dl_resp.headers.get("content-disposition", "")
    assert dl_resp.content.startswith(b"%PDF")

    # Test Preview endpoint
    prev_resp = client.get(f"/api/pdf/preview/{filename}")
    assert prev_resp.status_code == 200
    assert prev_resp.headers["content-type"] == "application/pdf"
    assert "inline" in prev_resp.headers.get("content-disposition", "")
    assert prev_resp.content.startswith(b"%PDF")


def test_chat_pdf_intent_trigger_inference(client):
    report_data = {
        "report_name": "Executive Sales Summary",
        "quarter": "Q3 2026",
        "total_revenue": "$2.4M",
        "top_product": "AI Orchestration Platform"
    }

    # Keyword routed without explicit intent, but with trigger words and pdf_json in payload
    response = client.post(
        "/chat",
        json={
            "session_id": "session-pdf-2",
            "message": "Please generate pdf report from this data",
            "payload": {
                "pdf_json": report_data,
                "pdf_theme": "emerald"
            }
        }
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pdf_result"] is not None
    assert body["pdf_result"]["status"] == "success"


def test_chat_pdf_intent_missing_data(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "session-pdf-3",
            "intent": "pdf",
            "payload": {}
        }
    )
    # When payload is empty, endpoint returns 400 explaining that JSON values are required
    assert response.status_code == 400
    body = response.json()
    assert "PDF generation requires a JSON object" in body["detail"]


def test_pdf_download_not_found(client):
    response = client.get("/api/pdf/download/nonexistent_file_12345.pdf")
    assert response.status_code == 404


def test_get_pdf_templates_endpoint(client):
    response = client.get("/api/pdf/templates")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["count"] >= 2
    tpl_ids = [t["template_id"] for t in body["templates"]]
    assert "Vessel_Call_FDA_Exact_Format" in tpl_ids
    assert "Vessel_Call_PDA_Exact_Format" in tpl_ids


def test_chat_pdf_with_template_name(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "session-tpl-1",
            "intent": "pdf",
            "payload": {
                "template_name": "Vessel_Call_FDA_Exact_Format",
                "pdf_title": "Vessel Call FDA",
                "pdf_json": {
                    "vessel_name": "M/V PACIFIC HORIZON",
                    "port_name": "PORT OF SINGAPORE",
                    "total_disbursement": "20980.00",
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pdf_result"] is not None
    assert body["pdf_result"]["status"] == "success"
    assert body["pdf_result"]["page_count"] >= 1
