"""Tests for FTL Quote Estimator REST endpoints and agent integration."""
import base64
import json

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
    assert body["quote_result"]["Project Name"] == "120 Bloor St E"
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
    assert body["quote_result"]["Project Name"] == "77 King St W"
    assert "rendered_html" in body and body["rendered_html"] is not None
    assert "pdf_download_url" in body and body["pdf_download_url"].startswith("/api/ftl/quote/pdf/")
    assert "Sales Estimate" in body["reply"]


def test_quote_pdf_from_estimator_json(client):
    res = client.post(
        "/api/ftl/quote/pdf",
        json={
            "template_type": "inflow",
            "quote_result": {
                "estimate_number": "EST-900061",
                "project_name": "285-295 Coventry Modernization",
                "customer_name": "ATTA Elevators",
                "line_items": [
                    {
                        "product_code": "SGV2_CLUTCH_OTIS_LH",
                        "description": "CLUTCH + CAR DOOR LOCK (OTIS-L)",
                        "qty": 1,
                        "unit_price": 1010.4,
                    }
                ],
                "freight_estimate": 975.0,
            },
        },
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_base64_to_pdf(client):
    pdf_bytes = client.post(
        "/api/ftl/quote/pdf",
        json={"quote_result": {"estimate_number": "EST-1", "line_items": [{"product_code": "A", "qty": 1, "unit_price": 5}]}},
    ).content
    encoded = base64.b64encode(pdf_bytes).decode("ascii")

    res = client.post(
        "/api/ftl/base64-to-pdf",
        json={"pdf_base64": f"data:application/pdf;base64,{encoded}", "filename": "EST-1", "download": True},
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == 'attachment; filename="EST-1.pdf"'
    assert res.content == pdf_bytes


def test_title_case_keys_for_pdf_and_base64(client):
    quote = {
        "Estimate Number": "EST-2",
        "Line Items": [{"Product Code": "A", "Category": "Door Operator", "Qty": 2, "Unit Price": 5}],
    }
    pdf_res = client.post("/api/ftl/quote/pdf", json={"Quote Result": quote})
    assert pdf_res.status_code == 200, pdf_res.text
    assert pdf_res.content.startswith(b"%PDF")

    encoded = base64.b64encode(pdf_res.content).decode("ascii")
    res = client.post(
        "/api/ftl/base64-to-pdf",
        json={"Pdf Base64": encoded, "Filename": "EST-2", "Download": True},
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-disposition"] == 'attachment; filename="EST-2.pdf"'


def test_base64_to_pdf_rejects_bad_input(client):
    assert client.post("/api/ftl/base64-to-pdf", json={}).status_code == 400
    assert client.post("/api/ftl/base64-to-pdf", json={"pdf_base64": "not base64!!"}).status_code == 400
    not_pdf = base64.b64encode(b"hello").decode("ascii")
    assert client.post("/api/ftl/base64-to-pdf", json={"pdf_base64": not_pdf}).status_code == 400


def test_quote_pdf_requires_quote_result(client):
    res = client.post("/api/ftl/quote/pdf", json={"template_type": "inflow"})
    assert res.status_code == 400


def test_chat_quote_from_edited_qualifier_json(client, monkeypatch):
    seen = {}

    def fake_run_quote_estimation(skill, candidate_text, llm_overrides=None):
        seen["text"] = candidate_text
        return {
            "project_name": "285-295 Coventry - Modernization",
            "customer_name": "ATTA Elevators",
            "line_items": [
                {
                    "product_code": "SGV2_CLUTCH_OTIS_LH",
                    "description": "CLUTCH + CAR DOOR LOCK (OTIS-L)",
                    "quantity": 1,
                    "unit_price": 1010.4,
                }
            ],
            "freight": 0,
            "notes": [],
        }, 100

    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation", fake_run_quote_estimation
    )

    res = client.post(
        "/chat",
        json={
            "session_id": "test-1",
            "intent": "ftl_quote_estimator",
            "payload": {
                "template_type": "inflow",
                "qualifier_result": {
                    "qualify": "qualify",
                    "project_type": "modernization",
                    "project_name": "285-295 Coventry - Modernization",
                    "matched_items": [
                        {
                            "item": "clutch assembly",
                            "category": "clutch",
                            "match": "exact",
                            "note": "OTIS",
                        }
                    ],
                    "excluded_items": [
                        {"item": "sliding guide", "reason": "Not in the Wittur pricelist"}
                    ],
                    "flags": ["needs_engineering_review"],
                    "deadline": "2026-10-15",
                    "reasoning": "In scope clutch.",
                    "confidence": 0.85,
                },
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["quote_result"]["Project Name"] == "285-295 Coventry - Modernization"
    text = seen["text"]
    assert "clutch assembly" in text
    assert "sliding guide" in text
    assert "do not price" in text.lower()
    assert base64.b64decode(body["pdf_base64"]).startswith(b"%PDF")


def test_quote_accepts_public_qualifier_keys():
    from app.agents.ftl_quote_estimator_agent import render_qualifier_decision_for_quote

    text = render_qualifier_decision_for_quote(
        {
            "Qualify": "Needs Review",
            "Project Type": "Modernization",
            "Project Name": "Bloor Street Modernization",
            "Deadline": "2026-10-15",
            "Matched Items": [
                {"Item": "center opening door operator 42 inch", "Category": "Door Operator", "Match": "Exact"}
            ],
            "Excluded Items": [{"Item": "sliding guide", "Reason": "Not in the Wittur pricelist"}],
            "Flags": [],
            "Reasoning": "In-scope door equipment matches the catalog.",
            "Confidence": 90,
            "Ai Insight": "Strong door-package opportunity.",
        }
    )
    assert "Project name: Bloor Street Modernization" in text
    assert "Qualify decision: needs_review" in text
    assert "Project type: modernization" in text
    assert "category: door_operator" in text
    assert "center opening door operator 42 inch" in text
    assert "sliding guide" in text


def test_chat_pdf_base64_from_edited_quote_json(client, monkeypatch):
    def fail_run_quote_estimation(skill, candidate_text, llm_overrides=None):
        raise AssertionError("model must not be called for quote_result input")

    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation", fail_run_quote_estimation
    )

    res = client.post(
        "/chat",
        json={
            "session_id": "test-1",
            "intent": "ftl_quote_estimator",
            "payload": {
                "template_type": "internal_review",
                "quote_result": {
                    "estimate_number": "EST-900061",
                    "project_name": "285-295 Coventry Modernization",
                    "customer_name": "ATTA Elevators",
                    "line_items": [
                        {
                            "product_code": "SGV2_CLUTCH_OTIS_LH",
                            "description": "CLUTCH + CAR DOOR LOCK (OTIS-L)",
                            "qty": 2,
                            "unit_price": 100.0,
                        }
                    ],
                },
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pdf_filename"] == "EST-900061_internal_review.pdf"
    assert base64.b64decode(body["pdf_base64"]).startswith(b"%PDF")
    assert body["quote_result"]["Subtotal"] == 200.0


def test_chat_pdf_base64_from_quote_json_multipart(client):
    res = client.post(
        "/chat",
        files={
            "session_id": (None, "test-1"),
            "intent": (None, "ftl_quote_estimator"),
            "quote_result": (
                None,
                json.dumps(
                    {"estimate_number": "EST-1", "line_items": [{"product_code": "A", "qty": 1, "unit_price": 5}]}
                ),
            ),
        },
    )
    assert res.status_code == 200, res.text
    assert base64.b64decode(res.json()["pdf_base64"]).startswith(b"%PDF")
