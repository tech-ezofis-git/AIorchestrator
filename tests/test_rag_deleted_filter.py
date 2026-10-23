"""Deep Hits are dropped when the live repository row is marked deleted."""
import pytest

from app.global_search.rag_search import drop_deleted_document_hits, item_marked_deleted
from app.global_search.types import SearchHit


class _Db:
    def __init__(self, flag: str):
        self._flag = flag

    async def fetch(self, query, *args):
        if "starts_with" in query:
            return [{"table_schema": "repository", "table_name": "items_abcd1234"}]
        if "information_schema.columns" in query:
            return [
                {"column_name": "itemid", "data_type": "uuid", "udt_name": "uuid"},
                {"column_name": "isdeleted", "data_type": "integer", "udt_name": "int4"},
            ]
        return []

    async def fetchrow(self, query, *args):
        return {"deleted": self._flag}


def _hit() -> SearchHit:
    return SearchHit(
        type="document",
        entity_type="document",
        entity_id="item-1",
        entity_name="20170509083834691.pdf",
        matchSource="content",
        id={"itemId": "item-1", "repositoryId": "abcd1234-1111-1111-1111-111111111111"},
    )


@pytest.mark.asyncio
async def test_deleted_repository_row_is_dropped():
    hits = await drop_deleted_document_hits(_Db("1"), [_hit()])
    assert hits == []
    assert await item_marked_deleted(
        _Db("true"),
        item_id="item-1",
        repository_id="abcd1234-1111-1111-1111-111111111111",
    )


@pytest.mark.asyncio
async def test_live_repository_row_is_kept():
    hits = await drop_deleted_document_hits(_Db("0"), [_hit()])
    assert len(hits) == 1
    assert hits[0].entity_name == "20170509083834691.pdf"
