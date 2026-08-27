"""AP metadata PATCH payload builder + client wiring."""
import pytest

from app.ap_skills.ap_metadata import build_ap_metadata_fields, push_extract_metadata
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
    assert header["PO Number"] == "PO-9"
    assert header["Supplier"] == "Acme"
    assert header["Vendor Name"] == "Acme"
    assert header["Invoice Amount"] == 100.5
    assert header["Currency"] == "USD"
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


@pytest.mark.asyncio
async def test_push_extract_metadata_warns_when_form_ids_missing():
    seen = {}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "mock": True, "ezfbFieldsUpdated": 0}

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="t1",
        document_job={
            "workflow_id": "wf",
            "instance_id": "inst",
            "repository_id": "repo",
            "repository_item_id": "item-guid",
        },
        form_id=None,
        invoice={"invoice_number": "INV-1"},
    )
    assert result["ok"] is True
    assert result["ezfb_warning"] == "missing_form_ids"
    assert seen["form_id"] is None
    assert seen["form_entry_id"] is None


def test_build_ap_metadata_fields_empty_invoice():
    assert build_ap_metadata_fields({}) == {}
    assert build_ap_metadata_fields({"line_items": []}) == {}


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
            "repository_item_id": "item-guid",
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
    assert seen["item_id"] == "item-guid"
    assert seen["form_entry_id"] == 42
    assert seen["fields"]["invoice_header"]["Invoice No"] == "INV-9"


@pytest.mark.asyncio
async def test_ezofis_apply_metadata_mock_when_live_disabled(monkeypatch):
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
        item_id="item",
        fields={"invoice_header": {"Invoice No": "1"}},
        form_id="form",
        form_entry_id=7,
    )
    assert result["ok"] is True
    assert result["mock"] is True
    assert result["formEntryId"] == 7
    get_settings.cache_clear()
