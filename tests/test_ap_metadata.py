"""AP metadata PATCH payload builder + client wiring."""
import json

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


def test_harvest_hangfire_pascal_case_and_json_string():
    from app.models.chat import harvest_hangfire_ticket_ids

    nested = json.dumps(
        {
            "FormId": "9a117b01-bb6d-4696-a627-a9fa84bb006e",
            "FormEntryId": 11,
            "ItemId": "4283e687-f32f-40c0-a67e-c213724b1702",
        }
    )
    found = harvest_hangfire_ticket_ids(
        {
            "SessionId": "s-1",
            "WorkflowId": "wf",
            "InstanceId": "inst",
            "RepositoryId": "repo",
            "startPayload": nested,
        }
    )
    assert found["session_id"] == "s-1"
    assert found["workflowId"] == "wf"
    assert found["instanceId"] == "inst"
    assert found["repositoryId"] == "repo"
    assert found["formId"] == "9a117b01-bb6d-4696-a627-a9fa84bb006e"
    assert found["formEntryId"] == 11
    assert found["itemId"] == "4283e687-f32f-40c0-a67e-c213724b1702"
    assert found["repositoryItemId"] == "4283e687-f32f-40c0-a67e-c213724b1702"


def test_map_header_to_ezfb_columns_matches_underscore_and_jsonid():
    from app.ap_skills.store import _map_header_to_ezfb_columns

    assignments = _map_header_to_ezfb_columns(
        header={"Invoice No": "INV-10", "PO Number": "PO-10", "Supplier": "Acme"},
        columns=["item_id", "Invoice_No", "PO_Number", "Vendor_Name", "createdat"],
        form_controls=[
            {"name": "Vendor Name", "column_name": "Vendor_Name", "json_id": "vnd"},
            {"name": "Supplier", "column_name": "Vendor_Name", "json_id": "vnd"},
        ],
    )
    assert assignments["Invoice_No"] == "INV-10"
    assert assignments["PO_Number"] == "PO-10"
    assert assignments["Vendor_Name"] == "Acme"
    assert "createdat" not in assignments
    assert "item_id" not in assignments


def test_pick_repository_pk_skips_integer_id():
    from app.ap_skills.store import guid_compact, guid_hyphenate, pick_repository_item_pk

    assert guid_compact("9c06f762-16f5-4c00-9560-d50e1f6b3eac") == "9c06f76216f54c009560d50e1f6b3eac"
    assert guid_hyphenate("9c06f76216f54c009560d50e1f6b3eac") == "9c06f762-16f5-4c00-9560-d50e1f6b3eac"
    assert (
        pick_repository_item_pk(
            ["id", "itemid", "Invoice_No", "FileName"],
            {"id": "integer", "itemid": "uuid", "Invoice_No": "text", "FileName": "text"},
        )
        == "itemid"
    )
    assert pick_repository_item_pk(["id", "Invoice_No"], {"id": "uuid", "Invoice_No": "text"}) == "id"
    assert (
        pick_repository_item_pk(
            ["id", "itemid", "Invoice_No"],
            {
                "id": "integer int4",
                "itemid": "USER-DEFINED uniqueidentifier",
                "Invoice_No": "text text",
            },
        )
        == "itemid"
    )


def test_repository_item_match_sql_uses_itemid_and_filepath():
    from app.ap_skills.store import repository_guid_columns, repository_item_match_sql

    columns = ["id", "itemid", "Invoice_No", "FilePath"]
    types = {
        "id": "integer int4",
        "itemid": "USER-DEFINED uniqueidentifier",
        "Invoice_No": "text text",
        "FilePath": "text text",
    }
    assert repository_guid_columns(columns, types) == ["itemid"]
    sql = repository_item_match_sql(columns, types, param=5)
    assert "regexp_replace" not in sql
    assert "replace(" in sql
    assert '"itemid"' in sql
    assert "FilePath" in sql or '"FilePath"' in sql
    assert "$5" in sql


def test_ezfb_pk_match_guid_and_integer():
    from app.ap_skills.store import ezfb_pk_match

    guid_sql, guid_val = ezfb_pk_match('"item_id"', "bbbbbbbb-1111-2222-3333-444444444444", 3)
    assert "::uuid" not in guid_sql
    assert "replace(" in guid_sql
    assert guid_val == "bbbbbbbb111122223333444444444444"
    int_sql, int_val = ezfb_pk_match('"item_id"', "12", 3)
    assert int_sql == '"item_id" = $3'
    assert int_val == 12


def test_row_mostly_empty_ignores_repository_file_columns():
    from app.ap_skills.store import _REPO_SKIP_COLS, _row_mostly_empty

    row = {
        "id": 1,
        "itemid": "9c06f762-16f5-4c00-9560-d50e1f6b3eac",
        "FileName": "INV-2026-6001.pdf",
        "FilePath": "repository/048f6cfc/9c06f76216f54c009560d50e1f6b3eac.pdf",
        "Invoice_No": None,
        "PO_Number": None,
    }
    assert _row_mostly_empty(row, skip_columns=_REPO_SKIP_COLS) is True
    assert _row_mostly_empty(row) is False


def test_map_header_skips_repository_file_columns():
    from app.ap_skills.store import _REPO_SKIP_COLS, _map_header_to_ezfb_columns

    assignments = _map_header_to_ezfb_columns(
        header={"Invoice No": "INV-1", "FileName": "scan.pdf", "FilePath": "repository/x/y.pdf"},
        columns=["id", "Invoice_No", "FileName", "FilePath"],
        form_controls=[],
        skip_columns=_REPO_SKIP_COLS,
    )
    assert assignments["Invoice_No"] == "INV-1"
    assert "FileName" not in assignments
    assert "FilePath" not in assignments


def test_repository_header_aliases_match_v6_item_columns():
    from app.ap_skills.store import (
        _REPO_SKIP_COLS,
        _map_header_to_ezfb_columns,
        expand_repository_header_aliases,
    )

    header = expand_repository_header_aliases(
        {
            "Invoice No": "INV-2026-6001",
            "PO Number": "PO-60001",
            "Vendor Name": "APEX INDUSTRIAL",
            "Invoice Amount": "5203.65",
            "Invoice Date": "2026-05-20",
        }
    )
    assert header["InvoiceNumber"] == "INV-2026-6001"
    assert header["PoNumber"] == "PO-60001"
    assert header["Supplier"] == "APEX INDUSTRIAL"
    assert header["Amount"] == "5203.65"
    assert header["DocumentDate"] == "2026-05-20"

    assignments = _map_header_to_ezfb_columns(
        header=header,
        columns=["id", "InvoiceNumber", "PoNumber", "Supplier", "Amount", "DocumentDate", "file_name"],
        form_controls=[],
        skip_columns=_REPO_SKIP_COLS,
    )
    assert assignments["InvoiceNumber"] == "INV-2026-6001"
    assert assignments["PoNumber"] == "PO-60001"
    assert assignments["Supplier"] == "APEX INDUSTRIAL"
    assert assignments["Amount"] == "5203.65"
    assert assignments["DocumentDate"] == "2026-05-20"
    assert "file_name" not in assignments


def test_parse_repo_decimal_strips_cad_and_skip_ocr_score():
    from decimal import Decimal

    from app.ap_skills.store import _REPO_SKIP_COLS, _parse_repo_decimal, coerce_repository_assignment

    assert _parse_repo_decimal("5,653.65 CAD") == Decimal("5653.65")
    assert coerce_repository_assignment("InvoiceAmount", "1582.00", "text") == "1582.00"
    assert coerce_repository_assignment("DueDate", "2026-06-20", "date").isoformat() == "2026-06-20"
    assert "ocr_score" in _REPO_SKIP_COLS
    assert "total_pages" in _REPO_SKIP_COLS


@pytest.mark.asyncio
async def test_push_writes_ezfb_even_when_workflow_ids_missing():
    seen = {}

    class FakeStore:
        async def apply_ezfb_item_fields(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "updated": 1, "table": "ezfb_formguid_items", "columns": ["Invoice_No"]}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("PATCH should not run without workflow ids")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        document_job={"form_id": "form-guid", "form_entry_id": "10"},
        form_id="form-guid",
        invoice={"invoice_number": "INV-10", "po_number": "PO-10", "vendor": "Acme"},
        store=FakeStore(),
    )
    assert result["ok"] is True
    assert result["ezfb"]["updated"] == 1
    assert seen["form_entry_id"] == 10
    assert seen["header"]["Invoice No"] == "INV-10"


@pytest.mark.asyncio
async def test_push_writes_repository_items_table():
    seen = {}

    class FakeStore:
        async def apply_ezfb_item_fields(self, **kwargs):
            return {"ok": True, "updated": 1, "table": "ezfb_9a117b01_items"}

        async def apply_repository_item_fields(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "updated": 1, "table": "repository.items_38b1b6dd"}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("PATCH should not run without workflow ids")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        document_job={
            "form_id": "9a117b01-bb6d-4696-a627-a9fa84bb006e",
            "form_entry_id": "12",
            "repository_id": "38b1b6dd-854b-489f-aa44-ac6d4dd691e8",
            "repository_item_id": "4283e687-f32f-40c0-a67e-c213724b1702",
        },
        form_id="9a117b01-bb6d-4696-a627-a9fa84bb006e",
        invoice={"invoice_number": "INV-12", "po_number": "PO-12", "vendor": "Acme"},
        store=FakeStore(),
    )
    assert result["ok"] is True
    assert result["repository"]["table"] == "repository.items_38b1b6dd"
    assert seen["item_id"] == "4283e687-f32f-40c0-a67e-c213724b1702"
    assert seen["repository_id"] == "38b1b6dd-854b-489f-aa44-ac6d4dd691e8"
    assert seen["header"]["Invoice No"] == "INV-12"
    assert seen["header"]["InvoiceNumber"] == "INV-12"
    assert seen["header"]["PoNumber"] == "PO-12"
    assert seen["header"]["Supplier"] == "Acme"


@pytest.mark.asyncio
async def test_push_writes_repository_with_compact_item_guid():
    seen = {}

    class FakeStore:
        async def apply_ezfb_item_fields(self, **kwargs):
            return {"ok": True, "updated": 1}

        async def apply_repository_item_fields(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "updated": 1, "table": "repository.items_048f6cfc"}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("PATCH should not run without workflow ids")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        document_job={
            "form_id": "36b59e8d-1dd3-400c-9ab5-7c887841f343",
            "form_entry_id": "2",
            "repository_id": "048f6cfc-7eb0-471c-aa54-cbb5f504c951",
            "item_id": "9c06f76216f54c009560d50e1f6b3eac",
        },
        form_id="36b59e8d-1dd3-400c-9ab5-7c887841f343",
        invoice={"invoice_number": "INV-2026-6001", "po_number": "PO-60001", "vendor": "Acme"},
        store=FakeStore(),
    )
    assert result["ok"] is True
    assert seen["item_id"] == "9c06f762-16f5-4c00-9560-d50e1f6b3eac"
    assert result["request"]["item_id"] == "9c06f762-16f5-4c00-9560-d50e1f6b3eac"


def test_resolve_metadata_ids_accepts_compact_item_guid():
    ids = resolve_metadata_ids(
        {
            "repository_id": "048f6cfc-7eb0-471c-aa54-cbb5f504c951",
            "item_id": "9c06f76216f54c009560d50e1f6b3eac",
            "form_id": "36b59e8d-1dd3-400c-9ab5-7c887841f343",
            "form_entry_id": 2,
        },
        form_id=None,
    )
    assert ids["item_id"] == "9c06f762-16f5-4c00-9560-d50e1f6b3eac"
    assert ids["form_entry_id"] == 2


@pytest.mark.asyncio
async def test_apply_repository_item_fields_matches_uniqueidentifier_and_filepath():
    from app.ap_skills.store import ApStore

    executed = {}

    class FakeDb:
        async def fetch(self, query, *args):
            q = query.lower()
            if "information_schema.columns" in q:
                return [
                    {"column_name": "id", "data_type": "integer", "udt_name": "int4"},
                    {
                        "column_name": "itemid",
                        "data_type": "USER-DEFINED",
                        "udt_name": "uniqueidentifier",
                    },
                    {"column_name": "Invoice_No", "data_type": "text", "udt_name": "text"},
                    {"column_name": "FilePath", "data_type": "text", "udt_name": "text"},
                ]
            return []

        async def fetchrow(self, query, *args):
            if "information_schema.tables" in query.lower():
                return {"table_schema": "repository", "table_name": "items_048f6cfc"}
            return None

        async def execute(self, query, *args):
            executed["sql"] = query
            executed["args"] = args
            return "UPDATE 1"

    result = await ApStore(FakeDb()).apply_repository_item_fields(
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        repository_id="048f6cfc-7eb0-471c-aa54-cbb5f504c951",
        item_id="9c06f762-16f5-4c00-9560-d50e1f6b3eac",
        header={"Invoice No": "INV-2026-6001", "PO Number": "PO-60001"},
    )
    assert result["ok"] is True
    assert result["updated"] == 1
    sql = executed["sql"]
    assert "regexp_replace" not in sql
    assert "replace(" in sql
    assert "itemid" in sql
    assert "FilePath" in sql
    assert executed["args"][-1] == "9c06f76216f54c009560d50e1f6b3eac"
    assert "INV-2026-6001" in executed["args"]


@pytest.mark.asyncio
async def test_apply_repository_item_fields_coerces_amount_date_and_scopes_row():
    from datetime import date
    from decimal import Decimal

    from app.ap_skills.store import ApStore

    executed = {}

    class FakeDb:
        async def fetch(self, query, *args):
            q = query.lower()
            if "information_schema.columns" in q:
                return [
                    {"column_name": "id", "data_type": "uuid", "udt_name": "uuid"},
                    {"column_name": "tenant_id", "data_type": "uuid", "udt_name": "uuid"},
                    {"column_name": "repository_id", "data_type": "uuid", "udt_name": "uuid"},
                    {"column_name": "is_deleted", "data_type": "boolean", "udt_name": "bool"},
                    {"column_name": "InvoiceNumber", "data_type": "text", "udt_name": "text"},
                    {"column_name": "PoNumber", "data_type": "text", "udt_name": "text"},
                    {"column_name": "Supplier", "data_type": "text", "udt_name": "text"},
                    {"column_name": "Amount", "data_type": "numeric", "udt_name": "numeric"},
                    {"column_name": "DocumentDate", "data_type": "date", "udt_name": "date"},
                ]
            return []

        async def fetchrow(self, query, *args):
            if "information_schema.tables" in query.lower():
                return {"table_schema": "repository", "table_name": "items_048f6cfc"}
            return None

        async def execute(self, query, *args):
            executed["sql"] = query
            executed["args"] = args
            return "UPDATE 1"

    result = await ApStore(FakeDb()).apply_repository_item_fields(
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        repository_id="048f6cfc-7eb0-471c-aa54-cbb5f504c951",
        item_id="9c06f762-16f5-4c00-9560-d50e1f6b3eac",
        header={
            "Invoice No": "INV-2026-6001",
            "PO Number": "PO-60001",
            "Vendor Name": "APEX INDUSTRIAL",
            "Invoice Amount": "5,203.65",
            "Invoice Date": "2026-05-20",
        },
    )
    assert result["ok"] is True
    sql = executed["sql"]
    args = list(executed["args"])
    assert "InvoiceNumber" in sql
    assert "tenant_id" in sql
    assert "repository_id" in sql
    assert "IS NOT TRUE" in sql
    assert Decimal("5203.65") in args
    assert date(2026, 5, 20) in args
    assert "INV-2026-6001" in args
    assert args[-2] == "2e3b7b3738a34f94878ea006dad93230"
    assert args[-1] == "048f6cfc7eb0471caa54cbb5f504c951"
    assert "9c06f76216f54c009560d50e1f6b3eac" in args


@pytest.mark.asyncio
async def test_apply_ezfb_item_fields_matches_guid_without_uuid_cast():
    from app.ap_skills.store import ApStore

    executed = {}

    class FakeDb:
        async def fetch(self, query, *args):
            q = query.lower()
            if "information_schema.columns" in q:
                return [
                    {"column_name": "item_id", "data_type": "USER-DEFINED", "udt_name": "uniqueidentifier"},
                    {"column_name": "Invoice_No", "data_type": "text", "udt_name": "text"},
                ]
            if "information_schema.tables" in q:
                return [{"table_schema": "dbo", "table_name": "ezfb_36b59e8d_items"}]
            return []

        async def fetchrow(self, query, *args):
            q = query.lower()
            if "information_schema.tables" in q:
                return {"table_schema": "dbo", "table_name": "ezfb_36b59e8d_items"}
            if "select 1" in q:
                return {"ok": 1}
            return None

        async def execute(self, query, *args):
            executed["sql"] = query
            executed["args"] = args
            return "UPDATE 1"

    result = await ApStore(FakeDb()).apply_ezfb_item_fields(
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        form_id="36b59e8d-1dd3-400c-9ab5-7c887841f343",
        form_entry_id="bbbbbbbb-1111-2222-3333-444444444444",
        header={"Invoice No": "INV-2026-6001"},
    )
    assert result["ok"] is True
    assert "::uuid" not in executed["sql"]
    assert "replace(" in executed["sql"]
    assert executed["args"][-1] == "bbbbbbbb111122223333444444444444"


@pytest.mark.asyncio
async def test_push_uses_latest_empty_ezfb_row_when_form_entry_missing():
    seen = {}

    class FakeStore:
        async def fetch_ticket_context(self, **kwargs):
            return {}

        async def latest_empty_ezfb_item(self, **kwargs):
            return 11

        async def apply_ezfb_item_fields(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "updated": 1, "table": "ezfb_9a117b01_items", "form_entry_id": 11}

    class FakeEz:
        async def apply_ap_agent_metadata(self, **kwargs):
            raise AssertionError("PATCH should not run without workflow ids")

    result = await push_extract_metadata(
        ezofis=FakeEz(),
        tenant_id="2e3b7b37-38a3-4f94-878e-a006dad93230",
        document_job={"form_id": "9a117b01-bb6d-4696-a627-a9fa84bb006e"},
        form_id="9a117b01-bb6d-4696-a627-a9fa84bb006e",
        invoice={"invoice_number": "INV-11", "po_number": "PO-11", "vendor": "Acme"},
        store=FakeStore(),
    )
    assert result["ok"] is True
    assert seen["form_entry_id"] == 11
    assert result["request"]["form_entry_source"] == "latest_empty_row"


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
    assert seen["fields"]["invoice_header"]["InvoiceNumber"] == "INV-9"
    assert seen["fields"]["invoice_header"]["PoNumber"] == "PO-1"
    assert seen["fields"]["invoice_header"]["Supplier"] == "Acme"


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


def test_metadata_skips_label_used_as_value():
    fields = build_ap_metadata_fields(
        {"invoice_header": {"Terms": "Terms", "Currency": "USD", "Invoice No": "INV-12"}}
    )
    header = fields["invoice_header"]
    assert "Terms" not in header
    assert header["Currency"] == "USD"
    assert header["Invoice No"] == "INV-12"


def test_as_invoice_does_not_default_usd_when_extract_is_empty():
    from app.ap_skills.extract_invoice import _as_invoice

    inv = _as_invoice({"doc_type": "invoice"})
    assert not inv.get("invoice_number")
    assert not inv.get("currency")
    fields = build_ap_metadata_fields(inv)
    assert "Currency" not in (fields.get("invoice_header") or {})


def test_header_from_labeled_text_keeps_real_values():
    from app.ap_skills.extract_invoice import _header_from_labeled_text

    header = _header_from_labeled_text(
        "Invoice No: INV-12\nPO Number: PO-9\nTerms: Terms\nCurrency: USD\n"
    )
    assert header["Invoice No"] == "INV-12"
    assert header["PO Number"] == "PO-9"
    assert "Terms" not in header
    assert header["Currency"] == "USD"


def test_heuristic_reads_split_label_value_invoice_table():
    from app.ap_skills.ap_metadata import build_ap_metadata_fields
    from app.ap_skills.extract_invoice import _coalesce_invoice, _heuristic_from_text

    ocr_text = """
APEX INDUSTRIAL COMPONENTS LTD
615 Enterprise Parkway
Windsor, ON N8W 5K3
Canada
INVOICE
Bill To:
STERLING MANUFACTURING GROUP LTD.
Invoice #
PO #
Terms
Ship Via
Shipped
Due Date
INV-2026-6001
PO-60001
31
Fed Ground
05/20/26
06/20/26
Subtotal
4605.00
Tax (13%)
598.65
Invoice Total
5203.65
Canada CAD
"""
    inv = _heuristic_from_text(ocr_text)
    assert inv["invoice_number"] == "INV-2026-6001"
    assert inv["po_number"] == "PO-60001"
    assert inv["vendor"] == "APEX INDUSTRIAL COMPONENTS LTD"
    assert str(inv["total"]).replace(",", "") == "5203.65"
    assert inv["currency"] == "CAD"

    llm_miss = {
        "doc_type": "invoice",
        "invoice_number": "",
        "po_number": "",
        "vendor": "",
        "total": None,
        "currency": "",
        "invoice_header": {"Supplier #": "APC-T001", "INCOTERM": "FCA"},
    }
    merged, ungrounded = _coalesce_invoice(llm_miss, inv, ocr_text)
    assert ungrounded == []
    assert merged["invoice_number"] == "INV-2026-6001"
    assert merged["po_number"] == "PO-60001"
    fields = build_ap_metadata_fields(merged)
    header = fields["invoice_header"]
    assert header["Invoice No"] == "INV-2026-6001"
    assert header["PO Number"] == "PO-60001"
    assert header["Vendor Name"] == "APEX INDUSTRIAL COMPONENTS LTD"
    assert header["Invoice Amount"] == "5203.65"



def test_embedded_pdf_text_rejects_form_labels():
    from app.integrations.ocr_engine import embedded_pdf_text_is_usable

    labels = "PO Number\nInvoice No\nTerms\nCurrency\nMatched Status\nVendor Name"
    assert embedded_pdf_text_is_usable(labels) is False
    overlay = (
        "APC-T001\ninvoice\nTerms\nCurrency\nUSD\nPO Number\nInvoice No\n"
        "Vendor Name\n2026-08-31T12:23:24.291Z\nMatched Status\nNot Matched"
    )
    assert embedded_pdf_text_is_usable(overlay) is False
    invoice = (
        "ACME Supplies\nInvoice No: INV-100\nPO Number: PO-1\n"
        "Amount: 1234.56\nDate: 2026-08-31\nQty 10 widgets"
    )
    assert embedded_pdf_text_is_usable(invoice) is True


def test_hollow_extract_does_not_write_defaults():
    fields = build_ap_metadata_fields(
        {
            "doc_type": "invoice",
            "currency": "USD",
            "invoice_header": {"Terms": "Terms", "Currency": "USD", "Document Type": "invoice"},
        },
        extras={"Matched Status": "Not Matched"},
    )
    assert fields == {}
