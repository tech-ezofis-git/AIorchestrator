"""AP document job on POST /chat intent=ap — skills, credits, re-runs, legacy Q&A."""
import json
from datetime import datetime, timezone

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
        "workflow_move_next",
    ]
    assert result["decision"] == "MATCHED"
    assert result["artifacts"]["workflow_move_next"]["skipped"] is True
    assert result["artifacts"]["workflow_move_next"]["reason"] == "no instance_id"
    assert len(charges) == 6
    assert [c["skill_id"] for c in charges] == [
        "extract_invoice",
        "po_match",
        "duplicate_detect",
        "vendor_validate",
        "backorder_detect",
        "finalize_decision",
    ]
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


def test_ap_formid_alias_is_passed_to_po_lookup(client, monkeypatch):
    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )
    guid = "29171de4-e210-466e-9e90-40fa9fa4354d"
    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-formid",
            "intent": "ap",
            "payload": _ap_payload(formid=guid),
        },
    )
    assert response.status_code == 200, response.text
    po = response.json()["ap_result"]["artifacts"]["po_match"]["po"]
    assert po["form_id"] == guid
    assert po["ezfb_table"] == "ezfb_29171de4_items"


def test_ap_numeric_form_id_selects_ezfb_table(client, monkeypatch):
    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-form-num",
            "intent": "ap",
            "payload": _ap_payload(form_id="98"),
        },
    )
    assert response.status_code == 200, response.text
    po = response.json()["ap_result"]["artifacts"]["po_match"]["po"]
    assert po["form_id"] == "98"
    assert po["ezfb_table"] == "ezfb_98_items"


def test_explicit_skills_without_backorder_never_runs_or_charges(client, monkeypatch):
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
            "session_id": "s-ap-gate",
            "intent": "ap",
            "payload": _ap_payload(
                skills=[
                    "extract_invoice",
                    "po_match",
                    "duplicate_detect",
                    "vendor_validate",
                    "finalize_decision",
                ],
            ),
        },
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
    assert result["skills_run"][-1] == "workflow_move_next"
    assert result["artifacts"]["workflow_move_next"]["skipped"] is True
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


def test_phase2_gl_and_grn_run_when_explicitly_requested(client, monkeypatch):
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
            "payload": _ap_payload(
                item_id="doc-p2",
                invoice_json=invoice,
                skills=PHASE2_MATCH_ENABLED,
            ),
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


def test_explicit_skills_without_grn_never_runs_or_charges_grn(client, monkeypatch):
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked"}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    skills = [s for s in PHASE2_MATCH_ENABLED if s != "grn_match"]
    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-no-grn",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-no-grn", skills=skills),
        },
    )
    assert response.status_code == 200, response.text
    ran = response.json()["ap_result"]["skills_run"]
    assert "grn_match" not in ran
    assert "grn_match" not in charges
    assert "gl_match" in ran


def test_gl_only_rerun_uses_stored_extract_artifact(client, monkeypatch):
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


def test_default_pipeline_calls_move_next_when_instance_id_set(client, monkeypatch):
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
            "session_id": "s-ap-default-move",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-default-move",
                instance_id="inst-1",
                activityid="DR97uPaylMtwahvi3XYr_",
            ),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["skills_run"][-1] == "workflow_move_next"
    assert result["credits_charged"] == 7
    assert len(move_calls) == 1
    assert move_calls[0]["instance_id"] == "inst-1"
    assert move_calls[0]["payload"]["activityid"] == "DR97uPaylMtwahvi3XYr_"
    assert "skipped" not in result["artifacts"]["workflow_move_next"]


def test_move_next_forwards_apagent_workflow_ids(client, monkeypatch):
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
            "session_id": "s-ap-wf-ids",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-wf-ids",
                workflowId="wf-guid",
                instanceId="inst-guid",
                repository="repo-guid",
                transactionId="100",
                formentryId="42",
                repositoryItemId="item-guid",
                processId="200",
                activityid="DR97uPaylMtwahvi3XYr_",
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert len(move_calls) == 1
    body = move_calls[0]["payload"]
    assert body["workflowId"] == "wf-guid"
    assert body["instanceId"] == "inst-guid"
    assert body["repositoryId"] == "repo-guid"
    assert body["transactionId"] == "100"
    assert body["formEntryId"] == "42"
    assert body["itemId"] == "item-guid"
    assert body["processId"] == "200"
    assert body["activityid"] == "DR97uPaylMtwahvi3XYr_"
    assert body["isItemTable"] is True
    assert body["review"] in ("Matched", "Partially Matched", "Not Matched", "Non-Invoice")
    assert "comments" in body
    assert body["AIAGENTResponse"]["decision"] == body["review"]
    assert "decision" not in body
    assert "item_key" not in body
    assert "run_id" not in body


def test_move_next_looks_up_activityid_from_workflow_steps(client, monkeypatch):
    client.fake_db_pool.workflow_steps.append(
        {
            "name": "AP AGENT 1",
            "workflow_id": "wf-guid",
            "activity_id": "DR97uPaylMtwahvi3XYr_",
            "order": 1,
        }
    )
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
            "session_id": "s-ap-activity-db",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-act-db",
                workflowId="wf-guid",
                instanceId="inst-guid",
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert move_calls[0]["payload"]["activityid"] == "DR97uPaylMtwahvi3XYr_"


def test_move_next_payload_activityid_overrides_db_lookup(client, monkeypatch):
    client.fake_db_pool.workflow_steps.append(
        {
            "name": "AP AGENT 1",
            "workflow_id": "wf-guid",
            "activity_id": "act-from-db",
            "order": 1,
        }
    )
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
            "session_id": "s-ap-activity-override",
            "intent": "ap",
            "payload": _ap_payload(
                item_id="doc-act-override",
                workflowId="wf-guid",
                instanceId="inst-guid",
                activityid="act-from-payload",
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert move_calls[0]["payload"]["activityid"] == "act-from-payload"


def test_move_next_skips_http_when_activityid_missing(client, monkeypatch):
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
            "session_id": "s-ap-no-activity",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-no-act", instance_id="inst-1"),
        },
    )
    assert response.status_code == 200, response.text
    artifact = response.json()["ap_result"]["artifacts"]["workflow_move_next"]
    assert artifact["skipped"] is True
    assert artifact["reason"] == "no activityid"
    assert move_calls == []


def test_workflow_move_next_mocked_after_finalize(client, monkeypatch):
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
                activityid="DR97uPaylMtwahvi3XYr_",
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
    assert move_calls[0]["payload"]["review"] in ("Matched", "Partially Matched", "Not Matched")


def test_non_invoice_path_skips_match_skills_when_requested(client, monkeypatch):
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
                activityid="DR97uPaylMtwahvi3XYr_",
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


def test_metadata_push_runs_after_every_skill_with_non_null_values(client, monkeypatch):
    seen = []

    async def capture_meta(self, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "mock": True, "ezfbFieldsUpdated": 4}

    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.apply_ap_agent_metadata",
        capture_meta,
    )
    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    skills = ["extract_invoice", "po_match", "finalize_decision"]
    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-meta-all",
            "intent": "ap",
            "payload": _ap_payload(
                workflow_id="wf",
                instance_id="inst",
                repository_id="repo",
                repository_item_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                form_entry_id="42",
                form_id="form-guid",
                skills=skills,
            ),
        },
    )
    assert response.status_code == 200, response.text
    artifacts = response.json()["ap_result"]["artifacts"]
    assert [call["fields"]["invoice_header"]["Invoice No"] for call in seen] == ["INV-100"] * 3
    assert len(seen) == 3
    for skill_id in skills:
        assert artifacts[skill_id]["metadata_push"]["ok"] is True
        header = seen[skills.index(skill_id)]["fields"]["invoice_header"]
        assert header["Invoice No"] == "INV-100"
        assert header["PO Number"] == "PO-1"
        assert header["Supplier"] == "ACME Supplies"
        assert None not in header.values()
        assert "" not in header.values()
    assert seen[-1]["fields"]["invoice_header"]["Matched Status"] == "Matched"


def test_hangfire_start_payload_aliases_reach_metadata(client, monkeypatch):
    seen = []

    async def capture_meta(self, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "mock": True, "ezfbFieldsUpdated": 4}

    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.apply_ap_agent_metadata",
        capture_meta,
    )
    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-hangfire",
            "intent": "ap",
            "startPayload": {
                "tenantId": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "workflowId": "wf",
                "instanceId": "inst",
                "repositoryId": "repo",
                "itemId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "formId": "form-guid",
                "formData": {"formEntryId": 9},
                "invoice_json": SAMPLE_INVOICE,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert seen
    assert seen[0]["form_id"] == "form-guid"
    assert seen[0]["form_entry_id"] == 9
    assert seen[0]["item_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    header = seen[0]["fields"]["invoice_header"]
    assert header["Invoice No"] == "INV-100"
    assert header["PO_Number"] == "PO-1"
    assert None not in header.values()


def test_v6_root_ticket_ids_reach_metadata_patch(client, monkeypatch):
    seen = []

    async def capture_meta(self, **kwargs):
        seen.append(kwargs)
        return {
            "ok": True,
            "mock": True,
            "ezfbFieldsUpdated": 9,
            "repositoryFieldsUpdated": 7,
            "lineItemsUpdated": True,
        }

    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.apply_ap_agent_metadata",
        capture_meta,
    )
    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-ap-root-ids",
            "intent": "ap",
            "workflowId": "967f9423-ac93-4c70-93cb-df500f0d4cc9",
            "instanceId": "a96efa0d-28f1-4b48-afc2-c9791a346ce9",
            "repositoryId": "38b1b6dd-854b-489f-aa44-ac6d4dd691e8",
            "itemId": "4283e687-f32f-40c0-a67e-c213724b1702",
            "formId": "9a117b01-bb6d-4696-a627-a9fa84bb006e",
            "formEntryId": 10,
            "payload": {
                "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "invoice_json": SAMPLE_INVOICE,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert seen
    call = seen[0]
    assert call["repository_id"] == "38b1b6dd-854b-489f-aa44-ac6d4dd691e8"
    assert call["item_id"] == "4283e687-f32f-40c0-a67e-c213724b1702"
    assert call["form_id"] == "9a117b01-bb6d-4696-a627-a9fa84bb006e"
    assert call["form_entry_id"] == 10
    assert call["workflow_id"] == "967f9423-ac93-4c70-93cb-df500f0d4cc9"
    assert call["instance_id"] == "a96efa0d-28f1-4b48-afc2-c9791a346ce9"
    assert call["fields"]["invoice_header"]["Invoice No"] == "INV-100"
    assert call["fields"]["invoice_header"]["PO Number"] == "PO-1"
    assert call["fields"]["Invoice Extracted Line Item"][0]["description"] == "Widget"


def test_hangfire_pascal_case_ids_reach_metadata_patch(client, monkeypatch):
    seen = []

    async def capture_meta(self, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "mock": True, "ezfbFieldsUpdated": 9}

    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.apply_ap_agent_metadata",
        capture_meta,
    )
    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={
            "sessionId": "s-ap-pascal",
            "intent": "ap",
            "WorkflowId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee1",
            "InstanceId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee2",
            "RepositoryId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee3",
            "ItemId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee4",
            "FormId": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "FormEntryId": 11,
            "payload": {
                "tenantId": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "invoice_json": SAMPLE_INVOICE,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert seen
    call = seen[0]
    assert call["workflow_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee1"
    assert call["instance_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee2"
    assert call["repository_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee3"
    assert call["item_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee4"
    assert call["form_id"] == "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    assert call["form_entry_id"] == 11
    assert call["fields"]["invoice_header"]["Invoice No"] == "INV-100"


def test_extract_invoice_llm_token_usage_is_captured_and_billed(client, monkeypatch):
    """Code-review finding #5: extract_invoice's LLM structuring call's
    real token usage must reach both ap_result.token_usage and the credit
    charge sent for that skill — previously always hardcoded to 0/None."""
    charge_calls = []

    async def tracking_charge(self, **kwargs):
        charge_calls.append(kwargs)
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    async def fake_completion(self, messages, **_kwargs):
        return {
            "content": json.dumps(
                {
                    "invoice_number": "INV-9",
                    "vendor": "Acme",
                    "po_number": "PO-1",
                    "total": 42.0,
                }
            ),
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-token-usage",
            "intent": "ap",
            "payload": {"tenant_id": "t-ap", "item_id": "doc-tokens", "filepath": "invoice.pdf"},
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["token_usage"] == {
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
    }

    extract_charge = next(c for c in charge_calls if c["skill_id"] == "extract_invoice")
    assert extract_charge["usage"] == {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}
    # Skills that made no LLM call bill no tokens.
    other_charge = next(c for c in charge_calls if c["skill_id"] == "po_match")
    assert other_charge["usage"] is None


def test_credit_ledger_write_failure_does_not_abort_the_run(client, monkeypatch):
    """Code-review finding #9: the external credit charge already happened
    by the time record_credit() runs — a local DB failure there must not
    abort/fail the whole run (which would risk a double-charge on retry).
    It's logged for reconciliation and the run completes normally."""
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    from app.ap_skills.store import ApStore

    original_record_credit = ApStore.record_credit
    calls = {"n": 0}

    async def flaky_record_credit(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated ledger write failure")
        return await original_record_credit(self, **kwargs)

    monkeypatch.setattr(ApStore, "record_credit", flaky_record_credit)

    response = client.post(
        "/chat",
        json={"session_id": "s-orphan", "intent": "ap", "payload": _ap_payload(item_id="doc-orphan")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["status"] == "completed"
    # All 6 skills still charged externally despite the first ledger write failing.
    assert len(charges) == 6
    assert result["credits_charged"] == 6


def test_empty_extraction_reports_completed_low_confidence(client, monkeypatch):
    """Code-review finding #3: a run whose extraction found none of
    {invoice_number, vendor, po_number, total} must not report the same
    "completed" status as a clean run — it should degrade to
    "completed_low_confidence" instead of silently looking fully processed."""
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-low-confidence",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-empty", invoice_json={"doc_type": "invoice"}),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["status"] == "completed_low_confidence"
    assert result["data_quality"]["low_confidence"] is True
    assert result["data_quality"]["extract"]["fields_found"] == 0

    run_row = client.fake_db_pool.ap_runs[result["run_id"]]
    assert run_row["status"] == "completed_low_confidence"
    assert run_row["data_quality"]["low_confidence"] is True


def test_normal_extraction_still_reports_completed(client, monkeypatch):
    """A clean extraction (all 4 key fields found) keeps reporting plain
    "completed" — the quality gate must not downgrade a good run."""

    async def tracking_charge(self, **kwargs):
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    response = client.post(
        "/chat",
        json={"session_id": "s-normal-quality", "intent": "ap", "payload": _ap_payload(item_id="doc-good")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["ap_result"]
    assert result["status"] == "completed"
    assert result["data_quality"]["low_confidence"] is False


def test_duplicate_default_pipeline_submission_is_deduplicated_within_window(client, monkeypatch):
    """Code-review finding #2: resubmitting the SAME default pipeline for
    the same (tenant_id, item_id) shortly after it already completed must
    not re-run skills, re-push metadata, or re-charge credits — it should
    short-circuit to the prior run's stored result."""
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    first = client.post(
        "/chat",
        json={"session_id": "s-dedupe-1", "intent": "ap", "payload": _ap_payload(item_id="doc-dedupe")},
    )
    assert first.status_code == 200, first.text
    first_result = first.json()["ap_result"]
    assert len(charges) == 6
    charges.clear()

    second = client.post(
        "/chat",
        # Different session_id — AP identity is (tenant_id, item_key), not
        # session_id, so this is exactly the "retried by a different
        # caller/session" scenario the finding describes.
        json={"session_id": "s-dedupe-2", "intent": "ap", "payload": _ap_payload(item_id="doc-dedupe")},
    )
    assert second.status_code == 200, second.text
    second_result = second.json()["ap_result"]

    assert second_result["deduplicated"] is True
    assert second_result["run_id"] == first_result["run_id"]
    assert second_result["decision"] == first_result["decision"]
    assert second_result["credits_charged"] == first_result["credits_charged"]
    # No skill re-executed, no credit re-charged, no metadata re-pushed.
    assert charges == []


def test_force_rerun_bypasses_dedupe_window(client, monkeypatch):
    """payload.force_rerun explicitly opts out of the dedupe short-circuit
    — a legitimate re-extraction (e.g. after fixing bad source data) must
    still actually run."""
    charges = []

    async def tracking_charge(self, **kwargs):
        charges.append(kwargs["skill_id"])
        return {"status": "mocked", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.charge_activity_credit",
        tracking_charge,
    )

    first = client.post(
        "/chat",
        json={"session_id": "s-force-1", "intent": "ap", "payload": _ap_payload(item_id="doc-force")},
    )
    assert first.status_code == 200, first.text
    charges.clear()

    second = client.post(
        "/chat",
        json={
            "session_id": "s-force-2",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-force", force_rerun=True),
        },
    )
    assert second.status_code == 200, second.text
    second_result = second.json()["ap_result"]

    assert "deduplicated" not in second_result
    assert len(charges) == 6


def test_concurrent_duplicate_ap_submission_returns_409(client):
    """Code-review finding #2: a genuinely concurrent duplicate — a second
    submission for the same (tenant_id, item_id) while the first is still
    "running" — is rejected outright (409), never silently double-run."""
    client.fake_db_pool.ap_runs["already-running"] = {
        "id": "already-running",
        "session_id": "s-race-1",
        "tenant_id": "t-ap",
        "item_key": "doc-race",
        "requested_skills": [],
        "status": "running",
        "decision": None,
        "credits_charged": 0,
        "data_quality": None,
        "created_at": datetime.now(timezone.utc),
        "finished_at": None,
    }

    response = client.post(
        "/chat",
        json={
            "session_id": "s-race-2",
            "intent": "ap",
            "payload": _ap_payload(item_id="doc-race"),
        },
    )

    assert response.status_code == 409, response.text
    assert "already in progress" in response.json()["detail"]
