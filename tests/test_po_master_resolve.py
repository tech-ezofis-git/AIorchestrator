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
