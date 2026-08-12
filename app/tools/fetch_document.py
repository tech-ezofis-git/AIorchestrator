"""fetch_document tool — ToolSchema-conformant wrapper around
EzofisClient.fetch_document (mocked; see app/integrations/ezofis_client.py
for the real-integration TODO). Registered with the Dispatcher in
app/main.py's lifespan and called only through it — agents never call
ezofis_client directly.
"""
from typing import Any

from app.integrations.ezofis_client import EzofisClient
from app.models.tool_schema import ToolSchema

FETCH_DOCUMENT_SCHEMA = ToolSchema(
    name="fetch_document",
    description="Fetch a document's content and metadata from EZOFIS by document id.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "The EZOFIS document identifier."},
        },
        "required": ["document_id"],
    },
    requires_confirmation=False,
)


def make_fetch_document_handler(ezofis_client: EzofisClient):
    """Returns an async handler closing over `ezofis_client`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, document_id: str) -> dict[str, Any]:
        return await ezofis_client.fetch_document(document_id)

    return handler
