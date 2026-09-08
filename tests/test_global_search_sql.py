"""Global Search SQL: all text columns, items_* with specificId, PO-style fields."""
import asyncio

from app.global_search.schema import pick_id_column, pick_text_columns
from app.global_search.sql_search import search_document_metadata, search_repositories, search_workflows


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
            {"table_schema": "dbo", "table_name": "items_a6169a5c"},
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
            ("dbo", "items_a6169a5c"): [
                {"column_name": "ItemId", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "IFileName", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "PONumber", "data_type": "character varying", "udt_name": "varchar"},
                {"column_name": "IsDeleted", "data_type": "boolean", "udt_name": "bool"},
            ],
        }

    async def fetch(self, sql: str, *args):
        compact = " ".join(sql.split()).lower()
        self.last_sql = sql
        self.sqls.append(sql)
        if "from information_schema.tables" in compact:
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
        if "items_a6169a5c" in compact:
            return [
                {
                    "entity_id": "11111111-1111-1111-1111-111111111111",
                    "entity_name": "po.pdf",
                    "m0": "po.pdf",
                    "m1": "PO-60001",
                }
            ]
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


def test_search_finds_po_number_in_items_and_repo_workflow():
    db = _FakeTenantDb()
    repo_id = "a6169a5c-1468-4fb5-90a9-220082a89f2a"

    docs = asyncio.run(search_document_metadata(db, "PO-60001", specific_id=repo_id))
    assert len(docs) == 1
    assert docs[0].matched_value == "PO-60001"
    assert docs[0].matched_field == "PONumber"
    assert docs[0].id["itemId"] == "11111111-1111-1111-1111-111111111111"
    assert docs[0].id["repositoryId"] == repo_id
    items_sql = next(s for s in db.sqls if "items_a6169a5c" in s.lower() and "select" in s.lower() and "information_schema" not in s.lower())
    assert "repositoryid" not in items_sql.lower().replace("wrepositoryid", "")

    repos = asyncio.run(search_repositories(db, "PO-60001", specific_id=repo_id))
    assert len(repos) == 1
    assert "PO-60001" in repos[0].entity_name

    workflows = asyncio.run(search_workflows(db, "PO-60001"))
    assert len(workflows) == 1
    assert "PO-60001" in workflows[0].entity_name
