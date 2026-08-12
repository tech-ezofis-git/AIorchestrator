"""fetch_invoice_status tool — ToolSchema-conformant wrapper around
EzofisClient.fetch_invoice_status (mocked; see
app/integrations/ezofis_client.py for the real-integration TODO).
Registered with the Dispatcher in app/main.py's lifespan and called only
through it — agents never call ezofis_client directly.
"""
from typing import Any

from app.integrations.ezofis_client import EzofisClient
from app.models.tool_schema import ToolSchema

FETCH_INVOICE_STATUS_SCHEMA = ToolSchema(
    name="fetch_invoice_status",
    description="Fetch an invoice's accounts-payable status from EZOFIS by invoice reference.",
    parameters={
        "type": "object",
        "properties": {
            "invoice_reference": {
                "type": "string",
                "description": "The EZOFIS invoice reference, e.g. 'INV-1234'.",
            },
        },
        "required": ["invoice_reference"],
    },
    requires_confirmation=False,
)


def make_fetch_invoice_status_handler(ezofis_client: EzofisClient):
    """Returns an async handler closing over `ezofis_client`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, invoice_reference: str) -> dict[str, Any]:
        return await ezofis_client.fetch_invoice_status(invoice_reference)

    return handler
