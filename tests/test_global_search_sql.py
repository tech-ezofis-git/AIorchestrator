"""Global Search SQL: flat hits, PO fields, forms, identity enrichment."""
import asyncio

from app.global_search.schema import pick_id_column, pick_text_columns
from app.global_search.sql_search import (
    search_document_metadata,
    search_forms,
    search_repositories,
    search_workflows,
)


def test_pick_text_columns_includes_custom_varchar_fields():
    by_lower = {
        "itemid": "ItemId",
        "ifilename": "IFileName",
        "ponumber": "PONumber",
        "isdeleted": "IsDeleted",
        "createdat": "CreatedAt",
    }
    types = {
        "itemid": "uuid",
        "ifilename": "character varying",
        "ponumber": "character varying",
        "isdeleted": "boolean",
        "createdat": "timestamp without time zone",
    }
    cols = pick_text_columns(by_lower, types_by_lower=types)
    assert "PONumber" in cols
    assert "IFileName" in cols
    assert "ItemId" not in cols
    assert "IsDeleted" not in cols


def test_pick_id_column_prefers_itemid():
    assert pick_id_column({"itemid": "ItemId", "id": "Id"}) == "ItemId"


class _FakeTenantDb:
    def __init__(self):
        self.sqls: list[str] = []
        self.last_sql = ""
        self.tables = [
            {"table_schema": "dbo", "table_name": "wrepository"},
            {"table_schema": "dbo", "table_name": "wworkflow"},
            {"table_schema": "dbo", "table_name": "wform"},
            {"table_schema": "dbo", "table_name": "repositoryitem"},
            {"table_schema": "workflow", "table_name": "workflow_attachments_aabbccdd"},
            {"table_schema": "workflow", "table_name": "workflow_instances_aabbccdd"},
            {"table_schema": "dbo", "table_name": "items_a6169a5c"},
            {"table_schema": "dbo", "table_name": "ezfb_abcd1234_items"},
        ]
        self.columns = {
            ("dbo", "wrepository"): [
                {"column_name": "Id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "Name", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "Code", "data_type": "character varying", "udt_name": "varchar"},
            ],
            ("dbo", "wworkflow"): [
                {"column_name": "Id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "Name", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "Status", "data_type": "character varying", "udt_name": "varchar"},
            ],
            ("dbo", "wform"): [
                {"column_name": "Id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "Name", "data_type": "character varying", "udt_name": "varchar"},
            ],
            ("dbo", "repositoryitem"): [
                {"column_name": "Id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "ItemId", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "WorkflowId", "data_type": "integer", "udt_name": "int4"},
                {"column_name": "InstanceId", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "RequestNo", "data_type": "character varying", "udt_name": "varchar"},
            ],
            ("workflow", "workflow_attachments_aabbccdd"): [
                {"column_name": "id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "workflow_instance_id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "repository_id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "item_id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "file_name", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "is_deleted", "data_type": "boolean", "udt_name": "bool"},
                {"column_name": "created_at_utc", "data_type": "timestamp with time zone", "udt_name": "timestamptz"},
            ],
            ("workflow", "workflow_instances_aabbccdd"): [
                {"column_name": "id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "workflow_id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "workflow_name", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "reference_number", "data_type": "character varying", "udt_name": "varchar"},
            ],
            ("dbo", "items_a6169a5c"): [
                {"column_name": "ItemId", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "IFileName", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "PONumber", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "Description", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "ModifiedAt", "data_type": "timestamp without time zone", "udt_name": "timestamp"},
                {"column_name": "IsDeleted", "data_type": "boolean", "udt_name": "bool"},
                {"column_name": "RepositoryId", "data_type": "uuid", "udt_name": "uuid"},
            ],
            ("dbo", "ezfb_abcd1234_items"): [
                {"column_name": "Id", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "PO_Number", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "ModifiedAt", "data_type": "timestamp without time zone", "udt_name": "timestamp"},
                {"column_name": "CreatedAt", "data_type": "timestamp without time zone", "udt_name": "timestamp"},
            ],
        }
        self.attachment_item_ids = {"11111111111111111111111111111111"}
        self.attachment_by_file = True

    async def fetch(self, sql: str, *args):
        compact = " ".join(sql.split()).lower()
        self.last_sql = sql
        self.sqls.append(sql)
        if "from information_schema.tables" in compact:
            if "workflow_attachments_" in compact:
                return [
                    t
                    for t in self.tables
                    if t["table_name"].lower().startswith("workflow_attachments_")
                    or t["table_name"].lower().startswith("workflowattachments_")
                ]
            if "starts_with(lower(table_name), 'ezfb_')" in compact or (
                "ezfb_" in compact and "like" in compact
            ):
                return [
                    t
                    for t in self.tables
                    if t["table_name"].lower().startswith("ezfb_")
                    and t["table_name"].lower().endswith("_items")
                ]
            names = set()
            if args and isinstance(args[0], (list, tuple)):
                names = {str(n).lower() for n in args[0]}
            elif args and isinstance(args[0], str) and (
                "like" in compact or "starts_with" in compact
            ):
                needle = str(args[0]).replace("\\", "").rstrip("%")
                return [t for t in self.tables if t["table_name"].lower().startswith(needle.lower())]
            return [t for t in self.tables if t["table_name"].lower() in names]
        if "from information_schema.columns" in compact:
            return list(self.columns.get((args[0], args[1]), []))
        if "workflow_attachments_aabbccdd" in compact and "select" in compact and "information_schema" not in compact:
            if "item_id" in compact and args:
                needle = str(args[0]).replace("-", "").lower()
                if needle in self.attachment_item_ids:
                    return [{"process_id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}]
            if "file_name" in compact and self.attachment_by_file and args:
                if "po.pdf" in str(args[0]).lower() or "inv-2026-6001.pdf" in str(args[0]).lower():
                    return [{"process_id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}]
            return []
        if "workflow_instances_aabbccdd" in compact and "select" in compact and "information_schema" not in compact:
            return [
                {
                    "instance_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "workflow_id": "aabbccdd-1111-2222-3333-444444444444",
                    "workflow_name": "AP Invoice Approval",
                    "request_no": "REQ-9001",
                }
            ]
        if "items_a6169a5c" in compact and "select" in compact and "information_schema" not in compact:
            return [
                {
                    "entity_id": "11111111-1111-1111-1111-111111111111",
                    "entity_name": "po.pdf",
                    "description": "INVOICE",
                    "modified_dt": "2026-08-05 12:00:00",
                    "created_dt": None,
                    "repository_id": "a6169a5c-1468-4fb5-90a9-220082a89f2a",
                    "m0": "po.pdf",
                    "m1": "PO-60001",
                    "m2": "INVOICE",
                }
            ]
        if "repositoryitem" in compact and "select" in compact and "information_schema" not in compact:
            return []
        if "ezfb_abcd1234_items" in compact and "select" in compact and "information_schema" not in compact:
            return [
                {
                    "entity_id": "703e14b7-2646-4679-af80-484d9ef57035",
                    "entity_name": "703e14b7-2646-4679-af80-484d9ef57035",
                    "description": "",
                    "modified_dt": "2026-09-07",
                    "created_dt": "2026-09-07",
                    "m0": "PO-60001",
                }
            ]
        if "wform" in compact and "form_name" in compact:
            return [
                {
                    "form_id": "abcd1234-0000-0000-0000-000000000001",
                    "form_name": "PO Master",
                }
            ]
        if "wrepository" in compact and " as n " in compact:
            return [{"n": "Invoices"}]
        if "wworkflow" in compact and " as n " in compact:
            return [{"n": "PO Approval"}]
        if "wrepository" in compact:
            return [
                {
                    "entity_id": "a6169a5c-1468-4fb5-90a9-220082a89f2a",
                    "entity_name": "PO-60001 Library",
                    "m0": "PO-60001 Library",
                    "m1": "PO",
                }
            ]
        if "wworkflow" in compact:
            return [
                {
                    "entity_id": "22222222-2222-2222-2222-222222222222",
                    "entity_name": "PO-60001 Approval",
                    "m0": "PO-60001 Approval",
                    "m1": "Active",
                }
            ]
        return []

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


def test_search_finds_po_number_with_repo_workflow_identity():
    db = _FakeTenantDb()
    repo_id = "a6169a5c-1468-4fb5-90a9-220082a89f2a"

    docs = asyncio.run(search_document_metadata(db, "PO-60001", specific_id=repo_id))
    assert len(docs) == 1
    assert docs[0].type == "document"
    assert docs[0].matched_value == "PO-60001"
    assert docs[0].description == "INVOICE"
    assert docs[0].modifiedDateandtime == "2026-08-05"
    assert docs[0].dateandtime == ""
    assert docs[0].id["itemId"] == "11111111-1111-1111-1111-111111111111"
    assert docs[0].id["repositoryId"] == repo_id
    assert docs[0].id["repositoryName"] == "Invoices"
    assert docs[0].id["workflowId"] == "aabbccdd-1111-2222-3333-444444444444"
    assert docs[0].id["workflowName"] == "AP Invoice Approval"
    assert docs[0].id["instanceId"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert docs[0].id["requestNo"] == "REQ-9001"
    assert "workspaceId" not in docs[0].id
    assert "processId" not in docs[0].id
    assert docs[0].name == "Invoices"

    repos = asyncio.run(search_repositories(db, "PO-60001", specific_id=repo_id))
    assert len(repos) == 1
    assert repos[0].type == "repository"
    assert repos[0].id["repositoryName"] == "PO-60001 Library"

    workflows = asyncio.run(search_workflows(db, "PO-60001"))
    assert len(workflows) == 1
    assert workflows[0].type == "workflow"
    assert workflows[0].id["workflowName"] == "PO-60001 Approval"


def test_document_hydrate_falls_back_to_file_name_in_attachments():
    """items_* hit id may differ from attachment.item_id; file_name still links the ticket."""
    db = _FakeTenantDb()
    db.attachment_item_ids = set()  # force item_id miss
    repo_id = "a6169a5c-1468-4fb5-90a9-220082a89f2a"

    docs = asyncio.run(search_document_metadata(db, "PO-60001", specific_id=repo_id))
    assert len(docs) == 1
    assert docs[0].id["workflowId"] == "aabbccdd-1111-2222-3333-444444444444"
    assert docs[0].id["workflowName"] == "AP Invoice Approval"
    assert docs[0].id["instanceId"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert docs[0].id["requestNo"] == "REQ-9001"


def test_search_forms_master_uses_wform_name_not_row_guid():
    db = _FakeTenantDb()
    forms = asyncio.run(search_forms(db, "PO-60001"))
    assert len(forms) == 1
    assert forms[0].type == "form"
    assert forms[0].formKind == "master"
    assert forms[0].id["formEntryId"] == "703e14b7-2646-4679-af80-484d9ef57035"
    assert forms[0].id["formId"] == "abcd1234-0000-0000-0000-000000000001"
    assert forms[0].id["masterFormId"] == "abcd1234-0000-0000-0000-000000000001"
    assert forms[0].id["formName"] == "PO Master"
    assert forms[0].id["masterFormName"] == "PO Master"
    assert forms[0].name == "PO Master"
    assert forms[0].id["formName"] != forms[0].id["formEntryId"]
    assert forms[0].id["masterFormId"] != forms[0].id["formEntryId"]
