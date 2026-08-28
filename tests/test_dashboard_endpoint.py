"""Dashboard agent: call 1 schema from items-table columns, call 2 hydrates data."""
from datetime import date

from app.core.intent_router import Intent, IntentRouter
from app.dashboard.widgets import fields_from_extract, hydrate_data, overlay_extract_artifacts, propose_widgets

TENANT_ID = "ca57657b-0000-0000-0000-000000000001"
REPO_ID = "38b1b6dd-854b-489f-aa44-ac6d4dd691e8"
WORKFLOW_ID = "4cf093e6-8b42-47da-9bf6-3ffadcdb15af"


async def test_dashboard_keyword_classifies_before_ap():
    router = IntentRouter()
    assert await router.classify("I need an AP dashboard") == Intent.DASHBOARD
    assert await router.classify("what's the status of invoice INV-1") == Intent.AP


def test_propose_widgets_from_ap_columns():
    kpis, charts, bound = propose_widgets(
        ["Supplier", "InvoiceAmount", "DueDate", "MatchedStatus", "InvoiceDate", "Currency"]
    )
    kpi_ids = [row["id"] for row in kpis]
    chart_ids = [row["id"] for row in charts]
    assert "total_ap" in kpi_ids
    assert "overdue" in kpi_ids
    assert "open_invoices" in kpi_ids
    assert "supplier_risk" in chart_ids
    assert bound["amount"] == "InvoiceAmount"
    assert bound["supplier"] == "Supplier"


def test_hydrate_sums_overdue_and_supplier():
    kpis = [
        {"id": "total_ap", "enabled": True},
        {"id": "overdue", "enabled": True},
        {"id": "open_invoices", "enabled": True},
    ]
    charts = [{"id": "supplier_risk", "enabled": True}]
    bound = {"amount": "InvoiceAmount", "due": "DueDate", "supplier": "Supplier"}
    data = hydrate_data(
        rows=[
            {"InvoiceAmount": "6200", "DueDate": date(2020, 1, 1), "Supplier": "Acme"},
            {"InvoiceAmount": "5300", "DueDate": date(2020, 1, 1), "Supplier": "Acme"},
        ],
        bound=bound,
        kpis=kpis,
        charts=charts,
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["total_ap"]["value"] == 11500
    assert data["kpis"]["overdue"]["value"] == 11500
    assert data["kpis"]["open_invoices"]["value"] == 2
    assert data["charts"]["supplier_risk"]["series"][0]["name"] == "Acme"


def test_dashboard_call1_schema_from_repository(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-1",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT_ID, "repository_id": REPO_ID},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["dashboard_result"]
    assert body["reply"].startswith("Suggested dashboard")
    assert result["phase"] == "schema"
    assert result["data"] is None
    assert result["table"] == "repository.items_38b1b6dd"
    assert result["repository_id"] == REPO_ID
    kpi_ids = [row["id"] for row in result["kpis"]]
    assert "total_ap" in kpi_ids
    assert "overdue" in kpi_ids
    assert any(row["id"] == "supplier_risk" for row in result["charts"])
    assert body["ap_result"] is None
    assert body["prompt_result"] is None


def test_dashboard_call1_schema_from_workflow(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-wf",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT_ID, "workflow_id": WORKFLOW_ID},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["dashboard_result"]
    assert result["phase"] == "schema"
    assert result["repository_id"] == REPO_ID
    assert result["workflow_id"] == WORKFLOW_ID
    assert result["table"] == "repository.items_38b1b6dd"


def test_dashboard_call2_hydrates_enabled_widgets(client):
    first = client.post(
        "/chat",
        json={
            "session_id": "s-dash-2",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT_ID, "repository_id": REPO_ID},
        },
    )
    assert first.status_code == 200, first.text
    schema = first.json()["dashboard_result"]
    for row in schema["kpis"]:
        row["enabled"] = row["id"] in {"total_ap", "overdue"}
    for row in schema["charts"]:
        row["enabled"] = row["id"] == "supplier_risk"

    second = client.post(
        "/chat",
        json={
            "session_id": "s-dash-2",
            "intent": "dashboard",
            "message": "apply",
            "payload": {
                "tenant_id": TENANT_ID,
                "repository_id": REPO_ID,
                "dashboard_json": schema,
            },
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    result = body["dashboard_result"]
    assert result["phase"] == "data"
    assert result["data"]["kpis"]["total_ap"]["value"] == 11500
    assert result["data"]["kpis"]["overdue"]["value"] == 11500
    assert "open_invoices" not in result["data"]["kpis"]
    assert result["data"]["charts"]["supplier_risk"]["series"][0]["value"] == 11500


def test_dashboard_requires_tenant_and_target(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-missing",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
        },
    )
    assert response.status_code == 400
    assert "tenant_id" in response.json()["detail"]


def test_extract_fields_from_ocr_invoice_total():
    fields = fields_from_extract(
        {
            "invoice": {"total": None, "vendor": "", "due_date": "", "currency": "USD"},
            "ocr_text": (
                "APEX INDUSTRIAL COMPONENTS LTD\n"
                "Due Date\nINV-2026-6001\n05/20/26\n06/20/26\n"
                "Invoice Total\n5203.65\n"
            ),
        }
    )
    assert fields["amount"] == 5203.65
    assert "APEX" in (fields["supplier"] or "")
    assert fields["due"] == date(2026, 6, 20)
    assert fields["invoice_date"] == date(2026, 5, 20)


def test_extract_fields_from_ocr_total_due():
    fields = fields_from_extract(
        {
            "invoice": {"total": None, "vendor": "VERTEX INDUSTRIAL SUPPLY LTD", "due_date": ""},
            "ocr_text": "Invoice Date\nJul 24 2026\nTERMS:\nNet One Month\nTotal Due: $949.20\n",
        }
    )
    assert fields["amount"] == 949.20
    assert fields["supplier"] == "VERTEX INDUSTRIAL SUPPLY LTD"
    assert fields["invoice_date"] == date(2026, 7, 24)
    assert fields["due"] == date(2026, 8, 23)


def test_overlay_fills_empty_item_columns():
    rows, used = overlay_extract_artifacts(
        rows=[{"id": "item-1", "InvoiceAmount": None, "Supplier": None, "DueDate": None}],
        bound={"amount": "InvoiceAmount", "supplier": "Supplier", "due": "DueDate"},
        artifacts_by_item={
            "item-1": {
                "extract_invoice": {
                    "invoice": {"total": None, "vendor": ""},
                    "ocr_text": "Vendor: Acme Ltd\nInvoice Total 1200.00\n",
                }
            }
        },
    )
    assert used is True
    assert rows[0]["InvoiceAmount"] == 1200.0
    assert rows[0]["Supplier"] == "Acme Ltd"


def test_dashboard_call2_hydrates_from_extract_when_amounts_empty(client):
    pool = client.fake_db_pool
    for row in pool.dashboard_items:
        row["InvoiceAmount"] = None
        row["Supplier"] = None
    pool.ap_skill_artifacts.extend(
        [
            {
                "tenant_id": TENANT_ID,
                "item_key": "item-1",
                "skill_id": "extract_invoice",
                "created_at": None,
                "result_json": {
                    "invoice": {"total": None, "vendor": "Acme Ltd"},
                    "ocr_text": "Invoice Total 6200\n",
                },
            },
            {
                "tenant_id": TENANT_ID,
                "item_key": "item-2",
                "skill_id": "extract_invoice",
                "created_at": None,
                "result_json": {
                    "invoice": {"total": None, "vendor": "Acme Ltd"},
                    "ocr_text": "Invoice Total 5300\n",
                },
            },
        ]
    )
    first = client.post(
        "/chat",
        json={
            "session_id": "s-dash-extract",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT_ID, "repository_id": REPO_ID},
        },
    )
    assert first.status_code == 200, first.text
    schema = first.json()["dashboard_result"]
    second = client.post(
        "/chat",
        json={
            "session_id": "s-dash-extract",
            "intent": "dashboard",
            "message": "apply",
            "payload": {
                "tenant_id": TENANT_ID,
                "repository_id": REPO_ID,
                "dashboard_json": schema,
            },
        },
    )
    assert second.status_code == 200, second.text
    result = second.json()["dashboard_result"]
    assert result["data_source"] == "ap_extract"
    assert result["data"]["kpis"]["total_ap"]["value"] == 11500
    assert result["data"]["charts"]["supplier_risk"]["series"][0]["name"] == "Acme Ltd"


def test_dashboard_unknown_repository(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-bad-repo",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {
                "tenant_id": TENANT_ID,
                "repository_id": "00000000-0000-0000-0000-000000000099",
            },
        },
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()
