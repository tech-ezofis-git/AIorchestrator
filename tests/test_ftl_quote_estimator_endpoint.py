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
    assert body["quote_result"]["Project"] == "120 Bloor St E"
    assert "rendered_html" not in body
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
    assert body["quote_result"]["Project"] == "77 King St W"
    assert "rendered_html" not in body
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


def test_chat_quote_uses_eml_and_ignores_qualifier_result(client, monkeypatch):
    seen = {}

    def fake_run_quote_estimation(skill, candidate_text, llm_overrides=None):
        seen["text"] = candidate_text
        return {
            "project_name": "120 Bloor Street East",
            "customer_name": "ATTA Elevators",
            "line_items": [
                {
                    "product_code": "RG100_CAR",
                    "description": "ROLLER GUIDE ASSEMBLY",
                    "quantity": 1,
                    "unit_price": 890.0,
                }
            ],
            "freight": 0,
            "notes": [],
        }, 100

    monkeypatch.setattr(
        "app.ftl.quote_estimator.agent.run_quote_estimation", fake_run_quote_estimation
    )

    eml = (
        "From: Julie House <julie@attaelevators.com>\r\n"
        "Subject: 120 Bloor Street East modernization\r\n"
        "Date: Mon, 6 Oct 2026 11:00:00 -0400\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Please quote new car roller guides for 120 Bloor Street East.\r\n"
    )
    res = client.post(
        "/chat",
        data={
            "session_id": "test-1",
            "intent": "ftl_quote_estimator",
            "template_type": "inflow",
            "qualifier_result": (
                '{"qualify":"qualify","project_name":"from qualifier",'
                '"matched_items":[{"item":"clutch assembly","category":"clutch","match":"exact"}],'
                '"excluded_items":[{"item":"sliding guide","reason":"Not in the Wittur pricelist"}]}'
            ),
        },
        files={"file": ("rfq.eml", eml.encode("utf-8"), "message/rfc822")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["quote_result"]["Project"] == "120 Bloor Street East"
    assert body["quote_result"]["Line Item"][0]["Product"] == "RG100_CAR"
    text = seen["text"]
    assert "Please quote new car roller guides" in text
    assert "120 Bloor Street East" in text
    assert "clutch assembly" not in text
    assert "sliding guide" not in text
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


def test_quote_accepts_renamed_qualifier_keys():
    from app.agents.ftl_quote_estimator_agent import render_qualifier_decision_for_quote

    text = render_qualifier_decision_for_quote(
        {
            "Qualify": "Qualify",
            "Project type": "Modernization",
            "Project": "3040 Wonderland Rd S",
            "Company Name": "ACME Elevators",
            "Matched items": [{"Item": "door detector", "Category": "Door Protective Device", "Match": "Exact"}],
            "Excluded items": [{"Item": "spirator closers", "Reason": "Not in the Wittur pricelist"}],
        }
    )
    assert "Project name: 3040 Wonderland Rd S" in text
    assert "Company name: ACME Elevators" in text
    assert "Project type: modernization" in text
    assert "door detector" in text
    assert "spirator closers" in text


def test_invoice_type_is_normalized_to_known_values():
    from app.ftl.quote_estimator.agent import _validate_quote_payload

    assert _validate_quote_payload({"line_items": [], "invoice_type": "proforma invoice"})["invoice_type"] == "Proforma Invoice"
    assert _validate_quote_payload({"line_items": [], "invoice_type": "Made Up"})["invoice_type"] == "Quotation"
    assert _validate_quote_payload({"line_items": []})["invoice_type"] == "Quotation"


def test_renamed_quote_keys_round_trip():
    from app.ftl.key_format import snake_keys, title_keys

    internal = {
        "customer_name": "ACME",
        "contact_name": "Jane",
        "contact_phone": "555",
        "estimate_number": "EST-1",
        "quote_date": "2026-09-28",
        "line_items": [{"product_code": "X_1", "qty": 1, "unit_price": 5.0, "subtotal": 5.0}],
    }
    public = title_keys(internal)
    assert public == {
        "Company Name": "ACME",
        "Contact": "Jane",
        "Phone Number": "555",
        "Order Number": "EST-1",
        "Date": "2026-09-28",
        "Line Item": [{"Product": "X_1", "Qty": 1, "Price": 5.0, "Subtotal": 5.0}],
    }
    assert snake_keys(public) == internal


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


def test_quote_estimator_applies_git_consistency_checks():
    from app.ftl.quote_estimator.agent import (
        _enforce_door_restrictor_bundling,
        _enforce_governor_scope_gate,
        _enforce_line_item_qty_matches_car_count,
        _enforce_panel_vs_adaptor_exclusivity,
        _enforce_two_speed_door_type,
        _enforce_wrg_gating,
        _normalize_door_tools_code,
    )
    from app.ftl.quote_estimator.extract import build_candidate_text

    two_speed = _enforce_two_speed_door_type(
        {
            "line_items": [
                {
                    "product_code": "SGV2_DOOR_OP_2C42_LH",
                    "category": "door_operator",
                    "qty": 1,
                    "unit_price": 1,
                    "description": "2C",
                }
            ]
        },
        "Door Configuration: Two-speed",
    )
    assert two_speed["line_items"][0]["product_code"] == "SGV2_DOOR_OP_2T42_LH"

    inventory = (
        "Existing Equipment Information\nCar 1\nCab Configuration: Single entrance\n"
    )
    qty = _enforce_line_item_qty_matches_car_count(
        {
            "line_items": [
                {"product_code": "SGV2_DOOR_OP_1S42_LH", "category": "door_operator", "qty": 2, "unit_price": 1}
            ],
            "assumptions": [],
        },
        inventory,
    )
    assert qty["line_items"][0]["qty"] == 1

    gated = _enforce_governor_scope_gate(
        {
            "line_items": [
                {"product_code": "WG_OL35_GOVERNOR", "category": "governor", "qty": 1, "unit_price": 100}
            ],
            "assumptions": [],
        },
        "New equipment such as machines, safeties, governors as required with a 10% margin.",
    )
    assert gated["line_items"] == []

    bundled = _enforce_door_restrictor_bundling(
        {
            "line_items": [
                {"product_code": "SGV2_DOOR_OP_1S42_LH", "category": "door_operator", "qty": 2, "unit_price": 10},
                {"product_code": "SGV2_CAR_DOOR_RESTRICTOR", "category": "other", "qty": 2, "unit_price": 0}
            ]
        }
    )
    restrictors = [it for it in bundled["line_items"] if it["product_code"] == "SGV2_CAR_DOOR_RESTRICTOR"]
    assert len(restrictors) == 1
    assert restrictors[0]["qty"] == 2
    assert restrictors[0]["unit_price"] == 0

    exclusive = _enforce_panel_vs_adaptor_exclusivity(
        {
            "line_items": [
                {"product_code": "PANEL", "category": "car_door_panel", "qty": 1, "unit_price": 1},
                {"product_code": "ADAPTOR", "category": "panel_adaptor", "qty": 1, "unit_price": 1},
            ],
            "assumptions": [],
        }
    )
    assert [it["category"] for it in exclusive["line_items"]] == ["car_door_panel"]

    stripped = _enforce_wrg_gating(
        {
            "line_items": [
                {"product_code": "WRG_MOTION_GEAR150", "category": "roller_guide", "qty": 4, "unit_price": 1, "description": "car"}
            ],
            "assumptions": [],
        },
        "Cab interior renovation only.",
    )
    assert stripped["line_items"] == []

    tools = _normalize_door_tools_code(
        {
            "line_items": [
                {
                    "product_code": "ALL WITTUR PROGRAMING TOOL",
                    "category": "door_tools",
                    "qty": 1,
                    "unit_price": 0,
                    "description": "tool",
                }
            ]
        }
    )
    assert tools["line_items"][0]["product_code"] == "SGV2_DOOR_TOOLS"

    shaw = "2.01 Existing Equipment Information\nCar 1\nDoor Configuration: Two-speed\n"
    assert "Existing Equipment Information" in build_candidate_text(shaw)["subsections"]


def test_short_parts_list_is_not_cut_down_to_one_window():
    from app.ftl.quote_estimator.extract import build_candidate_text

    products = [
        "door operator",
        "car door restrictor",
        "clutch",
        "universal car door panel",
        "door tools",
        "3D detector",
        "counterweight roller guides",
        "car roller guides",
        "car safeties",
        "governor",
        "tension sheave",
    ]
    gap = " filler" * 90
    body = gap.join(f"Provide a new {name} for each car." for name in products)
    assert len(body.split()) <= 1200
    candidate = build_candidate_text(body)
    assert "tension sheave" in candidate["fallback_text"]
    assert "door operator" in candidate["fallback_text"]


def test_thin_attachment_does_not_hide_the_email_product_list():
    from app.ftl.quote_estimator.extract import choose_eml_full_text

    products = (
        "door operator, clutch, car door restrictor, universal car door panel, "
        "door tools, 3D detector, roller guides, car safeties, governor, tension sheave"
    )
    body = f"Please quote each of these: {products}."
    thin_pdf = "Sheet 1. Door operator location only."
    assert "tension sheave" in choose_eml_full_text(thin_pdf, body)

    spec = (products + ". Confirm the existing equipment schedule before release. ") * 20
    assert len(spec) >= 800
    assert "please quote the clutch only" not in choose_eml_full_text(
        spec, "Please quote the clutch only."
    ).lower()


def test_html_parts_table_survives_a_short_plain_part():
    from email.message import EmailMessage

    from app.ftl.quote_estimator.extract import parse_eml_bytes

    msg = EmailMessage()
    msg["From"] = "sales@ftl-distribution.com"
    msg["Subject"] = "RFQ"
    msg.set_content("Please quote.")
    msg.add_alternative(
        "<html><body><table><tr><td>door operator</td><td>clutch</td>"
        "<td>car door restrictor</td><td>universal car door panel</td>"
        "<td>door tools</td><td>3D detector</td><td>roller guides</td>"
        "<td>car safeties</td><td>governor</td><td>tension sheave</td>"
        "</tr></table></body></html>",
        subtype="html",
    )
    parsed = parse_eml_bytes(msg.as_bytes())
    assert "tension sheave" in parsed["body_text"]
    assert "roller guides" in parsed["body_text"]


def test_one_line_quote_is_sent_back_when_the_rfq_names_the_full_set():
    from app.ftl.quote_estimator.agent import _incomplete_scope_nudge

    text = (
        "door operator, clutch, car door restrictor, universal car door panel, "
        "door tools, 3D detector, roller guides, car safeties, governor, tension sheave"
    )
    nudge = _incomplete_scope_nudge(
        {
            "line_items": [
                {
                    "product_code": "SGV2_DOOR_OP_2C42_L",
                    "category": "door_operator",
                    "description": "operator",
                }
            ]
        },
        text,
    )
    assert "incomplete" in nudge.lower()
    assert "governor" in nudge
    assert "roller guide" in nudge

    covered = _incomplete_scope_nudge(
        {
            "line_items": [
                {"product_code": "OP", "category": "door_operator", "description": "operator"},
                {"product_code": "CL", "category": "clutch", "description": "clutch"},
                {"product_code": "RG", "category": "roller_guide", "description": "roller guide"},
            ]
        },
        text,
    )
    assert covered == ""
