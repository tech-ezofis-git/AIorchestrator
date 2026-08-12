"""fetch_report_data tool — ToolSchema-conformant wrapper around
EzofisClient.fetch_report_data (mocked; see app/integrations/ezofis_client.py
for the real-integration TODO). Registered with the Dispatcher in
app/main.py's lifespan and called only through it — agents never call
ezofis_client directly.
"""
from typing import Any

from app.integrations.ezofis_client import EzofisClient
from app.models.tool_schema import ToolSchema

FETCH_REPORT_DATA_SCHEMA = ToolSchema(
    name="fetch_report_data",
    description="Fetch a report's labeled data points from EZOFIS by report id.",
    parameters={
        "type": "object",
        "properties": {
            "report_id": {"type": "string", "description": "The EZOFIS report identifier."},
        },
        "required": ["report_id"],
    },
    requires_confirmation=False,
)


def make_fetch_report_data_handler(ezofis_client: EzofisClient):
    """Returns an async handler closing over `ezofis_client`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, report_id: str) -> dict[str, Any]:
        return await ezofis_client.fetch_report_data(report_id)

    return handler
