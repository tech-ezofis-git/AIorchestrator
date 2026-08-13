"""AP document job on POST /chat intent=ap — skills, credits, re-runs, legacy Q&A."""
import json

SAMPLE_INVOICE = {
    "invoice_number": "INV-100",
    "vendor": "ACME Supplies",
    "po_number": "PO-1",
    "total": 1234.56,
    "currency": "USD",
    "line_items": [{"description": "Widget", "qty": 10, "amount": 1234.56}],
}


def _ap_payload(**extra):
    payload = {
        "tenant_id": "t-ap",
        "item_id": "doc-1",
        "invoice_json": SAMPLE_INVOICE,
    }
    payload.update(extra)
    return payload


def test_ap_document_job_happy_path_returns_ap_result(client, monkeypatch):
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs)
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={"session_id": "s-ap-doc", "intent": "ap", "payload": _ap_payload()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["ap_result"]
    assert result["credits_charged"] == 6
    assert result["skills_run"] == [
        "extract_invoice",
        "po_match",
        "duplicate_detect",
        "vendor_validate",
        "backorder_detect",
        "finalize_decision",
    ]
    assert result["decision"] == "MATCHED"
    assert len(charges) == 6
    assert [c["skill_id"] for c in charges] == result["skills_run"]
    assert json.loads(body["reply"])["run_id"] == result["run_id"]
    assert body["invoice_reference"] is None
    assert len(client.fake_db_pool.ap_credit_ledger) == 6


def test_ap_backorder_disabled_by_tenant_plan_never_runs_or_charges(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": [
            "extract_invoice",
            "po_match",
            "duplicate_detect",
            "vendor_validate",
            "finalize_decision",
        ],
        "thresholds": {},
    }
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked"}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={"session_id": "s-ap-gate", "intent": "ap", "payload": _ap_payload()},
    )
    assert response.status_code == 200, response.text
    skills = response.json()["ap_result"]["skills_run"]
    assert "backorder_detect" not in skills
    assert "backorder_detect" not in charges
    assert len(charges) == 5


def test_vendor_only_rerun_loads_extract_artifact(client, monkeypatch):
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked"}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    first = client.post(
        "/chat",
        json={"session_id": "s-ap-rerun", "intent": "ap", "payload": _ap_payload()},
    )
    assert first.status_code == 200, first.text
    charges.clear()

    second = client.post(
        "/chat",
        json={
            "session_id": "s-ap-rerun-2",
            "intent": "ap",
            "payload": {
                "tenant_id": "t-ap",
                "item_id": "doc-1",
                "skills": ["vendor_validate"],
            },
        },
    )
    assert second.status_code == 200, second.text
    result = second.json()["ap_result"]
    assert result["skills_run"] == ["vendor_validate"]
    assert result["credits_charged"] == 1
    assert charges == ["vendor_validate"]
    vendor = result["artifacts"]["vendor_validate"]
    assert vendor["status"] == "ACTIVE"
    assert vendor["vendor"] == "ACME Supplies"


def test_vendor_rerun_without_extract_artifact_fails_closed(client, monkeypatch):
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked"}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-missing",
            "intent": "ap",
            "payload": {
                "tenant_id": "t-ap",
                "item_id": "never-seen",
                "skills": ["vendor_validate"],
            },
        },
    )
    assert response.status_code == 400
    assert "extract_invoice" in response.json()["detail"]
    assert charges == []


def test_legacy_ap_status_qna_still_works(client, monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Invoice INV-1234 is currently Approved for $1,234.56, due 2026-07-01.",
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post(
        "/chat",
        json={"session_id": "s-ap-legacy", "message": "what's the status of invoice INV-1234"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invoice_reference"] == "INV-1234"
    assert body["ap_result"] is None


def test_multipart_ap_file_happy_path(client, monkeypatch):
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked"}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        data={
            "session_id": "s-ap-mp",
            "intent": "ap",
            "tenant_id": "t-ap",
            "item_id": "upload-1",
            "invoice_json": json.dumps(SAMPLE_INVOICE),
        },
        files={"file": ("invoice.txt", b"Invoice No INV-100", "text/plain")},
    )
    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["credits_charged"] == 6
    assert len(charges) == 6
