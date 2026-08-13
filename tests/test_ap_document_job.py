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


def test_uuid_tenant_document_job_uses_same_store_and_succeeds(client, monkeypatch):
    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-uuid",
            "intent": "ap",
            "payload": _ap_payload(
                tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
                item_id="doc-uuid-1",
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ap_result"]["tenant_id"] == "2e3b7b37-38a3-4f94-878e-a006dad93230"


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


PHASE2_MATCH_ENABLED = [
    "extract_invoice",
    "po_lookup_quickbooks",
    "po_match",
    "gl_match",
    "grn_match",
    "duplicate_detect",
    "vendor_validate",
    "matter_validate",
    "backorder_detect",
    "finalize_decision",
]

PHASE2_ENABLED = PHASE2_MATCH_ENABLED + [
    "workflow_progress",
    "workflow_move_next",
]


def test_phase2_gl_and_grn_run_when_tenant_enables_them(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": PHASE2_MATCH_ENABLED,
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

    invoice = dict(SAMPLE_INVOICE)
    invoice["matter_id"] = "M-001"
    invoice["line_items"] = [{"description": "Widget", "qty": 10, "amount": 1234.56, "gl_account": "6100"}]

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-p2",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-p2", invoice_json=invoice),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert "gl_match" in result["skills_run"]
    assert "grn_match" in result["skills_run"]
    assert "matter_validate" in result["skills_run"]
    assert "gl_match" in charges
    assert result["artifacts"]["gl_match"]["decision"] in ("MATCHED", "PARTIALLY_MATCHED")
    assert result["artifacts"]["matter_validate"]["status"] == "MATCHED"


def test_tenant_without_grn_never_runs_or_charges_grn(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": [s for s in PHASE2_MATCH_ENABLED if s != "grn_match"],
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
        json={"session_id": "s-ap-no-grn", "intent": "ap", "payload": _ap_payload(item_id="doc-no-grn")},
    )
    assert response.status_code == 200, response.text
    skills = response.json()["ap_result"]["skills_run"]
    assert "grn_match" not in skills
    assert "grn_match" not in charges
    assert "gl_match" in skills


def test_gl_only_rerun_uses_stored_extract_artifact(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": PHASE2_MATCH_ENABLED,
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

    first = client.post(
        "/chat",
        json={"session_id": "s-ap-gl1", "intent": "ap", "payload": _ap_payload(item_id="doc-gl")},
    )
    assert first.status_code == 200, first.text
    charges.clear()

    second = client.post(
        "/chat",
        json={
            "session_id": "s-ap-gl2",
            "intent": "ap",
            "payload": {
                "tenant_id": "t-ap",
                "item_id": "doc-gl",
                "skills": ["gl_match"],
            },
        },
    )
    assert second.status_code == 200, second.text
    result = second.json()["ap_result"]
    assert result["skills_run"] == ["gl_match"]
    assert result["credits_charged"] == 1
    assert charges == ["gl_match"]


def test_workflow_move_next_mocked_after_finalize(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": PHASE2_ENABLED,
        "thresholds": {},
    }
    move_calls = []

    async def tracking_move(self, **kwargs):
        move_calls.append(kwargs)
        return {"ok": True, "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.workflow_move_next",
        tracking_move,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-wf",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-wf",
                workflow_id="wf-1",
                instance_id="inst-1",
                skills=[
                    "extract_invoice",
                    "po_match",
                    "finalize_decision",
                    "workflow_progress",
                    "workflow_move_next",
                ],
            ),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["skills_run"][-1] == "workflow_move_next"
    assert len(move_calls) == 1
    assert move_calls[0]["instance_id"] == "inst-1"
    assert move_calls[0]["payload"]["review"] in ("MATCHED", "PARTIALLY_MATCHED", "NOT_MATCHED")


def test_non_invoice_path_skips_match_skills_when_requested(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": PHASE2_ENABLED,
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
        json={
            "session_id": "s-ap-non",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-non",
                invoice_json={"doc_type": "other", "invoice_number": "X-1"},
                instance_id="inst-non",
                skills=["extract_invoice", "finalize_decision", "workflow_move_next"],
            ),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["decision"] == "NON_INVOICE"
    assert "po_match" not in result["skills_run"]
    assert "gl_match" not in charges
    assert result["artifacts"]["workflow_move_next"]["review"] == "Non-Invoice"


def test_quickbooks_connector_lookup_feeds_po_match(client, monkeypatch):
    client.fake_db_pool.ap_tenant_plans["t-ap"] = {
        "enabled_skills": PHASE2_MATCH_ENABLED,
        "thresholds": {},
    }

    async def fake_qb(self, **kwargs):
        return {
            "po_number": kwargs["po_number"],
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "lines": [{"id": "1", "description": "Widget", "qty": 10, "amount": 1234.56}],
            "source": "quickbooks",
            "mock": True,
        }

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.lookup_po_quickbooks",
        fake_qb,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-qb",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-qb",
                resource="QUICKBOOKS",
                connector_id="conn-1",
                skills=["extract_invoice", "po_lookup_quickbooks", "po_match", "finalize_decision"],
            ),
        },
    )
    assert response.status_code == 200, response.text
    artifacts = response.json()["ap_result"]["artifacts"]
    assert artifacts["po_lookup_quickbooks"]["po"]["source"] == "quickbooks"
    assert artifacts["po_match"]["decision"] == "MATCHED"
