"""Tests for FTL Quote Estimator REST endpoints and agent integration."""
import pytest
from unittest.mock import patch


def test_quote_skill_endpoints(client):
    # GET skill
    res = client.get("/api/ftl/quote/skill")
    assert res.status_code == 200
    data = res.json()
    assert "instructions" in data
    assert "references" in data

    # PUT skill
    update_res = client.put(
        "/api/ftl/quote/skill",
        json={"instructions": "Updated test quoting instructions."},
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert "Updated test quoting instructions" in updated_data.get("instructions", "")


def test_quote_pricelist_status(client):
    res = client.get("/api/ftl/quote/pricelist/status")
    assert res.status_code == 200
    data = res.json()
    assert "indexed" in data
    assert "total_chunks" in data


def test_quotes_list(client):
    res = client.get("/api/ftl/quote/quotes")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_quote_direct_endpoint_and_pdf(client, monkeypatch):
    mock_quote = {
        "project_name": "120 Bloor St E",
        "customer_name": "Elevator Services Corp",
        "elevator_id": "Car #1",
        "quote_date": "2026-09-22",
        "line_items": [
            {
                "product_code": "HYDRA_PLUS_CO_42",
                "description": "Wittur Hydra Plus 2-Panel Center Opening Car Door Operator",
                "quantity": 1,
                "unit_price": 4200.0,
                "subtotal_override": None,
            },
            {
                "product_code": "SGV2_CAR_DOOR_RESTRICTOR",
                "description": "Car Door Restrictor (Bundled)",
                "quantity": 1,
                "unit_price": 0.0,
                "subtotal_override": 0.0,
            },
        ],
        "freight": 250.0,
        "notes": ["Standard warranty applies."],
    }

    def fake_run_quote_estimation(skill, candidate_text, llm_overrides=None):
        return mock_quote, 2100

    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation", fake_run_quote_estimation
    )

    # POST /api/ftl/quote
    res = client.post(
        "/api/ftl/quote",
        json={
            "filename": "120_bloor_spec.pdf",
            "candidate_text": "Sample specifications for 120 Bloor St E door operator replacement.",
            "template_type": "inflow",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["quote_result"]["project_name"] == "120 Bloor St E"
    assert "rendered_html" in body and ("<div" in body["rendered_html"].lower() or "<html" in body["rendered_html"].lower())
    estimate_number = body["estimate_number"]
    assert estimate_number is not None

    # Test PDF download endpoint
    pdf_res = client.get(f"/api/ftl/quote/pdf/{estimate_number}")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content.startswith(b"%PDF")


def test_chat_ftl_quote_estimator_intent(client, monkeypatch):
    mock_quote = {
        "project_name": "77 King St W",
        "customer_name": "Apex Elevator",
        "elevator_id": "Bank A - Car 2",
        "quote_date": "2026-09-22",
        "line_items": [
            {
                "product_code": "WRG_MOTION_GEAR150",
                "description": "Wittur Roller Guide 150mm",
                "quantity": 2,
                "unit_price": 850.0,
                "subtotal_override": None,
            }
        ],
        "freight": 150.0,
        "notes": ["Freight confirmed at order."],
    }

    def fake_run_quote_estimation(skill, candidate_text, llm_overrides=None):
        return mock_quote, 1800

    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation", fake_run_quote_estimation
    )

    res = client.post(
        "/chat",
        json={
            "session_id": "session-ftl-quote-1",
            "intent": "ftl_quote_estimator",
            "payload": {
                "candidate_text": "Specs for King St roller guides replacement.",
                "template_type": "internal_review",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "session-ftl-quote-1"
    assert "quote_result" in body and body["quote_result"] is not None
    assert body["quote_result"]["project_name"] == "77 King St W"
    assert "rendered_html" in body and body["rendered_html"] is not None
    assert "pdf_download_url" in body and body["pdf_download_url"].startswith("/api/ftl/quote/pdf/")
    assert "Sales Estimate" in body["reply"]
