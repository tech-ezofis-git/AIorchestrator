"""Tests for FTL RFQ Qualifier REST endpoints and agent integration."""
import pytest
from unittest.mock import patch


def test_qualifier_skill_endpoints(client):
    # GET skill
    res = client.get("/api/ftl/qualifier/skill")
    assert res.status_code == 200
    data = res.json()
    assert "instructions" in data
    assert "references" in data

    # PUT skill
    update_res = client.put(
        "/api/ftl/qualifier/skill",
        json={"instructions": "Updated test qualification instructions."},
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert "Updated test qualification instructions" in updated_data.get("instructions", "")


def test_qualifier_pricelist_status(client):
    res = client.get("/api/ftl/qualifier/pricelist/status")
    assert res.status_code == 200
    data = res.json()
    assert "indexed" in data
    assert "total_chunks" in data


def test_qualifier_runs_list(client):
    res = client.get("/api/ftl/qualifier/runs")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_qualifier_direct_endpoint(client, monkeypatch):
    mock_decision = {
        "qualify": "qualify",
        "project_type": "modernization",
        "matched_items": [
            {
                "item": "Wittur Hydra Plus 2-Panel Center Opening Door Operator",
                "category": "door_operator",
                "match": "exact",
                "catalog_ref": "HYDRA-PLUS-CO-42",
                "note": "Standard 42 inch opening",
            }
        ],
        "excluded_items": [],
        "flags": ["Standard lead time 4 weeks"],
        "deadline": "2026-10-15",
        "project_name": "Bloor St Modernization",
        "reasoning": "Standard modernization scope matching Wittur catalog items.",
        "confidence": 0.95,
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 1250

    monkeypatch.setattr(
        "app.ftl.qualifier.agent.run_qualification", fake_run_qualification
    )

    # Test POST /api/ftl/qualify with candidate text
    res = client.post(
        "/api/ftl/qualify",
        json={
            "filename": "rfq_spec.pdf",
            "candidate_text": "Sample modernization tender text for elevator door operators.",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["decision"]["qualify"] == "qualify"
    assert body["decision"]["project_name"] == "Bloor St Modernization"
    assert body["total_tokens"] == 1250
    assert "run_id" in body


def test_chat_ftl_qualifier_intent(client, monkeypatch):
    mock_decision = {
        "qualify": "needs_review",
        "project_type": "modernization",
        "matched_items": [],
        "excluded_items": [{"item": "Custom Cab Interior", "reason": "Not sold by FTL"}],
        "flags": ["Special glass panels requested"],
        "deadline": None,
        "project_name": "Riverdale Tower",
        "reasoning": "Mixed scope requiring manual review.",
        "confidence": 0.75,
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 980

    monkeypatch.setattr(
        "app.ftl.qualifier.agent.run_qualification", fake_run_qualification
    )

    res = client.post(
        "/chat",
        json={
            "session_id": "session-ftl-qual-1",
            "intent": "ftl_qualifier",
            "payload": {
                "candidate_text": "Riverdale Tower elevator modernization specs.",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "session-ftl-qual-1"
    assert "qualifier_result" in body and body["qualifier_result"] is not None
    assert body["qualifier_result"]["qualify"] == "needs_review"
    assert "NEEDS REVIEW" in body["reply"]
