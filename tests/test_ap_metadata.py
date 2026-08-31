"""AP metadata PATCH payload builder + client wiring."""
import pytest

from app.ap_skills.ap_metadata import (
    build_ap_metadata_fields,
    extras_from_artifacts,
    push_extract_metadata,
    resolve_metadata_ids,
)
from app.integrations.ezofis_client import EzofisClient


def test_build_ap_metadata_fields_maps_labels_and_line_items():
    fields = build_ap_metadata_fields(
        {
            "invoice_number": "INV-1",
            "po_number": "PO-9",
            "vendor": "Acme",
            "total": 100.5,
            "currency": "USD",
            "due_date": "2026-08-01",
            "line_items": [{"description": "Widget", "qty": 2, "amount": 100.5}],
        }
    )
    header = fields["invoice_header"]
    assert header["Invoice No"] == "INV-1"
    assert header["invoice_number"] == "INV-1"
    assert header["PO Number"] == "PO-9"
    assert header["Supplier"] == "Acme"
    assert header["Vendor Name"] == "Acme"
    assert header["Invoice Amount"] == "100.5"
    assert header["Currency"] == "USD"
    assert header["PO_Number"] == "PO-9"
    assert header["Invoice_No"] == "INV-1"
    assert "Matched Status" not in header
    lines = fields["Invoice Extracted Line Item"]
    assert lines == fields["Line Item"]
    assert len(lines) == 1
    assert lines[0]["description"] == "Widget"
    assert lines[0]["quantity"] == 2
    assert lines[0]["line_amount"] == 100.5


def test_build_ap_metadata_fields_preserves_nested_invoice_header():
    fields = build_ap_metadata_fields(
        {
            "invoice_header": {"Invoice No": "INV-2", "PO Number": "PO-2", "empty": ""},
            "Line Item": [{"description": "A", "quantity": "1"}],
        }
    )
    assert fields["invoice_header"]["Invoice No"] == "INV-2"
    assert fields["invoice_header"]["PO Number"] == "PO-2"
    assert "empty" not in fields["invoice_header"]
    assert fields["Invoice Extracted Line Item"][0]["description"] == "A"
    assert fields["Line Item"][0]["description"] == "A"


def test_build_ap_metadata_fields_skips_null_and_empty():
    fields = build_ap_metadata_fields(
        {
            "invoice_header": {
                "Invoice No": "INV-3",
                "PO Number": None,
                "Supplier": "",
                "Due Date": "   ",
            }
        }
    )
    header = fields["invoice_header"]
    assert header["Invoice No"] == "INV-3"
    assert "PO Number" not in header
    assert "Supplier" not in header
    assert "Due Date" not in header


def test_build_ap_metadata_fields_merges_skill_extras():
    fields = build_ap_metadata_fields(
        {"invoice_number": "INV-4", "vendor": "Acme"},
        extras={"Matched Status": "Matched"},
    )
    assert fields["invoice_header"]["Invoice No"] == "INV-4"
    assert fields["invoice_header"]["Matched Status"] == "Matched"


def test_extras_from_artifacts_uses_finalize_decision():
    extras = extras_from_artifacts(
        {
            "po_match": {"decision": "MATCHED"},
            "finalize_decision": {"decision": "PARTIALLY_MATCHED"},
        },
        "finalize_decision",
    )
    assert extras["Matched Status"] == "Partially Matched"


def test_form_control_aliases_copy_value_onto_column_and_jsonid():
    fields = build_ap_metadata_fields(
        {"invoice_number": "INV-5", "po_number": "PO-5"},
        form_controls=[
            {"name": "Invoice No", "column_name": "Invoice_No", "json_id": "invJson"},
            {"name": "PO Number", "column_name": "PO_Number", "json_id": "poJson"},
        ],
    )
    header = fields["invoice_header"]
    assert header["Invoice No"] == "INV-5"
    assert header["Invoice_No"] == "INV-5"
    assert header["invJson"] == "INV-5"
    assert header["PO Number"] == "PO-5"
    assert header["poJson"] == "PO-5"


def test_resolve_metadata_ids_reads_nested_form_data():
    ids = resolve_metadata_ids(
        {
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "form_id": "form-guid",
            "formData": {"formEntryId": "9"},
        },
        form_id=None,
    )
    assert ids["form_entry_id"] == 9


def test_build_ap_metadata_fields_empty_invoice():
    assert build_ap_metadata_fields({}) == {}
    assert build_ap_metadata_fields({"line_items": []}) == {}


def test_resolve_metadata_ids_prefers_repository_guid_and_numeric_item_as_form_entry():
    ids = resolve_metadata_ids(
        {
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "item_id": "42",
            "form_id": "9a117b01-1111-2222-3333-444444444444",
        },
        form_id=None,
    )
    assert ids["item_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ids["form_entry_id"] == 42
    assert ids["form_id"].startswith("9a117b01")


@pytest.mark.asyncio
async def test_push_extract_metadata_skips_when_ids_missing():
    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("should not call")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="t1",
        document_job={"workflow_id": "w1"},
        form_id=None,
        invoice={"invoice_number": "INV-1"},
    )
    assert result["skipped"] is True
    assert result["reason"] == "missing_ids"


@pytest.mark.asyncio
async def test_push_extract_metadata_skips_when_form_ids_missing():
    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("should not call — V6 requires form ids")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="t1",
        document_job={
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        },
        form_id=None,
        invoice={"invoice_number": "INV-1"},
    )
    assert result["skipped"] is True
    assert result["reason"] == "missing_form_ids"
    assert result["request"]["item_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.mark.asyncio
async def test_push_extract_metadata_calls_client():
    seen = {}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "mock": True, "ezfbFieldsUpdated": 3}

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="tenant-1",
        document_job={
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "form_entry_id": "42",
            "form_id": "form-guid",
        },
        form_id="form-guid",
        invoice={"invoice_number": "INV-9", "vendor": "Acme", "po_number": "PO-1"},
    )
    assert result["ok"] is True
    assert seen["tenant_id"] == "tenant-1"
    assert seen["workflow_id"] == "wf"
    assert seen["instance_id"] == "inst"
    assert seen["repository_id"] == "repo"
    assert seen["item_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert seen["form_entry_id"] == 42
    assert seen["fields"]["invoice_header"]["Invoice No"] == "INV-9"


@pytest.mark.asyncio
async def test_ezofis_apply_metadata_skips_without_form_ids(monkeypatch):
    monkeypatch.delenv("EZOFIS_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("EZOFIS_LOGIN_PASSWORD", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    client = EzofisClient()
    result = await client.apply_ap_agent_metadata(
        tenant_id="t1",
        workflow_id="wf",
        instance_id="inst",
        repository_id="repo",
        item_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        fields={"invoice_header": {"Invoice No": "1"}},
    )
    assert result["skipped"] is True
    assert result["reason"] == "missing_form_ids"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ezofis_apply_metadata_requires_login_when_form_ids_present(monkeypatch):
    monkeypatch.delenv("EZOFIS_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("EZOFIS_LOGIN_PASSWORD", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    client = EzofisClient()
    result = await client.apply_ap_agent_metadata(
        tenant_id="t1",
        workflow_id="wf",
        instance_id="inst",
        repository_id="repo",
        item_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        fields={"invoice_header": {"Invoice No": "1"}},
        form_id="form",
        form_entry_id=7,
    )
    assert result["skipped"] is True
    assert result["reason"] == "login_not_configured"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_push_extract_metadata_reports_login_not_configured(monkeypatch):
    monkeypatch.delenv("EZOFIS_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("EZOFIS_LOGIN_PASSWORD", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    result = await push_extract_metadata(
        ezofis=EzofisClient(),
        tenant_id="t1",
        document_job={
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "form_entry_id": "7",
            "form_id": "9a117b01-bb6d-4696-a627-a9fa84bb006e",
        },
        form_id="9a117b01-bb6d-4696-a627-a9fa84bb006e",
        invoice={"invoice_number": "INV-1", "vendor": "Acme"},
    )
    assert result["ok"] is False
    assert result["reason"] == "login_not_configured"
    assert result["ezfb_warning"] == "login_not_configured"
    get_settings.cache_clear()


def test_as_invoice_reads_nested_header_labels():
    from app.ap_skills.extract_invoice import _as_invoice

    inv = _as_invoice(
        {
            "invoice_header": {
                "Invoice No": "INV-9",
                "PO Number": "PO-1",
                "Vendor Name": "Acme",
                "Invoice Amount": 50,
            },
            "Line Item": [{"Description": "Widget", "Quantity": 2, "Extended": 50}],
        }
    )
    assert inv["invoice_number"] == "INV-9"
    assert inv["po_number"] == "PO-1"
    assert inv["vendor"] == "Acme"
    assert inv["total"] == 50
    assert inv["invoice_header"]["Invoice No"] == "INV-9"
    assert inv["line_items"][0]["description"] == "Widget"
    assert inv["line_items"][0]["qty"] == 2

    fields = build_ap_metadata_fields(inv)
    assert fields["invoice_header"]["Invoice No"] == "INV-9"
    assert fields["invoice_header"]["PO Number"] == "PO-1"
    assert fields["invoice_header"]["Invoice Amount"] == "50"
    assert fields["invoice_header"]["Vendor Name"] == "Acme"
    assert None not in fields["invoice_header"].values()
    assert "" not in fields["invoice_header"].values()
