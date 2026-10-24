"""Saved Report Library — generate, save, load cards from DB."""
from __future__ import annotations

from app.report_agent.library_store import infer_domain, row_to_card


def test_infer_domain_from_workflow_name():
    assert infer_domain(workflow_name="Accounts Payable") == "Accounts Payable"
    assert infer_domain(report_type="all_repositories") == "Document Management"
    assert infer_domain(report_type="all_workflows") == "Workflow Automation"


def test_library_card_matches_report_page_shape():
    card = row_to_card(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "AP Aging Report",
            "description": "Outstanding AP requests grouped by aging bucket.",
            "domain": "Accounts Payable",
            "owner": "console",
            "visibility": "Private",
            "tags": ["AP", "Aging"],
            "status": "Draft",
            "favorite": False,
            "views": 0,
            "runs": 0,
            "version": 1,
            "viz": "table",
            "schedule": None,
            "created_at": None,
            "modified_at": None,
            "last_generated_at": None,
        }
    )
    assert card["name"] == "AP Aging Report"
    assert card["domain"] == "Accounts Payable"
    assert card["status"] == "Draft"
    assert card["scheduled"] is False
    assert card["tags"] == ["AP", "Aging"]


def test_save_and_list_reports_from_db(client):
    payload = {
        "name": "AP Aging Report",
        "description": "Outstanding AP requests grouped by aging bucket (0-5, 6-15, 16-30, 30+ days).",
        "tenantId": "t-lib",
        "reportType": "specific_workflow",
        "workflowName": "Accounts Payable",
        "owner": "console",
        "reportPrompt": "Report: AP Aging",
        "reportSpec": {
            "title": "AP Aging Report",
            "objective": "Outstanding AP by aging bucket.",
            "columns": [{"key": "Invoice_No", "label": "Invoice No"}],
        },
    }
    saved = client.post("/api/report-agent/reports", json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["name"] == "AP Aging Report"
    assert body["status"] == "Draft"
    assert body["domain"] == "Accounts Payable"
    assert body["reportSpec"]["title"] == "AP Aging Report"

    listed = client.get("/api/report-agent/reports", params={"tenantId": "t-lib"})
    assert listed.status_code == 200
    cards = listed.json()
    assert len(cards) == 1
    assert cards[0]["name"] == "AP Aging Report"
    assert "reportSpec" not in cards[0]

    loaded = client.get(f"/api/report-agent/reports/{body['id']}", params={"tenantId": "t-lib"})
    assert loaded.status_code == 200
    assert loaded.json()["reportPrompt"] == "Report: AP Aging"

    published = client.patch(
        f"/api/report-agent/reports/{body['id']}",
        params={"tenantId": "t-lib"},
        json={"status": "Published"},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "Published"


def test_console_has_save_not_example_cards(client):
    response = client.get("/console")
    assert response.status_code == 200
    assert 'id="reportSaveBtn"' in response.text
    assert 'id="reportSavedSelect"' in response.text
    assert 'id="reportCatalogWrap"' not in response.text
    assert "Example reports" not in response.text
    assert "/api/report-agent/reports" in response.text
