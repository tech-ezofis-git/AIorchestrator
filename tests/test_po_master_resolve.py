"""Unit tests for AP_AGENT PoMaster extraction from workflowJson."""
from __future__ import annotations

from app.ap_skills.po_master_resolve import (
    ensure_master_form_id_on_job,
    extract_po_master_from_workflow_json,
)


SAMPLE = {
    "blocks": [
        {"type": "START", "settings": {"label": "Gmail"}},
        {
            "type": "AP_AGENT",
            "settings": {
                "label": "AP AGENT 1",
                "formId": "1e16dd88-28b9-4da4-b577-eb0ec6d4d621",
                "poMasterSourceType": "internal",
                "apAgent": {
                    "formId": "1e16dd88-28b9-4da4-b577-eb0ec6d4d621",
                    "resource": "FORM",
                },
            },
        },
    ]
}


def test_extract_po_master_from_ap_agent_block():
    source, form_id = extract_po_master_from_workflow_json(SAMPLE)
    assert source == "InternalForm"
    assert form_id == "1e16dd88-28b9-4da4-b577-eb0ec6d4d621"


def test_ensure_master_form_id_stamps_when_missing():
    job = {"form_id": "6e45749f-c65d-4f28-9e0f-f22fdde4cb03"}
    assert ensure_master_form_id_on_job(job, SAMPLE) is True
    assert job["master_form_id"] == "1e16dd88-28b9-4da4-b577-eb0ec6d4d621"
    assert job["master_source"] == "InternalForm"


def test_ensure_master_form_id_skips_when_present():
    job = {"master_form_id": "already"}
    assert ensure_master_form_id_on_job(job, SAMPLE) is False
    assert job["master_form_id"] == "already"


def test_prefers_ap_agent_form_over_invoice_settings_form_id():
    invoice = "1dea95c8-005f-400d-98e7-f85e6c515a6e"
    master = "719c181f-d861-4fb5-b10b-f5a9ffdfc657"
    workflow = {
        "blocks": [
            {
                "type": "AP_AGENT",
                "settings": {
                    "formId": invoice,
                    "apAgent": {"formId": master, "resource": "FORM"},
                },
            }
        ]
    }
    source, form_id = extract_po_master_from_workflow_json(workflow)
    assert source == "InternalForm"
    assert form_id == master
    job = {"form_id": invoice, "master_form_id": invoice}
    assert ensure_master_form_id_on_job(job, workflow) is True
    assert job["master_form_id"] == master


def test_accepts_workflow_json_string_and_invoice_id_hint():
    invoice = "eab8b2ab-f7a1-4aca-a066-e9a8f6fc23bb"
    master = "60af91ff-b082-4dc8-8f63-7094565cdc48"
    workflow = {
        "blocks": [
            {
                "type": "AP_AGENT",
                "settings": {"apAgent": {"formId": master, "resource": "FORM"}},
            }
        ]
    }
    import json

    body = {"workflowJson": json.dumps(workflow)}
    job = {"master_form_id": invoice, "workflow_id": "wf-1"}
    assert ensure_master_form_id_on_job(job, body, invoice_form_id=invoice) is True
    assert job["master_form_id"] == master
