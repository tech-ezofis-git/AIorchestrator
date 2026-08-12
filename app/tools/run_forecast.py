"""run_forecast tool — ToolSchema-conformant wrapper around
ForecastModelClient.run_forecast (mocked; see
app/integrations/forecast_model.py — no real forecasting model is chosen
yet). Registered with the Dispatcher in app/main.py's lifespan and called
only through it — agents never call the model client directly.
"""
from typing import Any

from app.integrations.forecast_model import ForecastModelClient
from app.models.tool_schema import ToolSchema

RUN_FORECAST_SCHEMA = ToolSchema(
    name="run_forecast",
    description="Forecast a metric over a future horizon.",
    parameters={
        "type": "object",
        "properties": {
            "metric": {"type": "string", "description": "The metric to forecast, e.g. 'revenue'."},
            "horizon": {"type": "string", "description": "The forecast horizon, e.g. '3 months', 'Q3'."},
        },
        "required": ["metric", "horizon"],
    },
    requires_confirmation=False,
)


def make_run_forecast_handler(forecast_model_client: ForecastModelClient):
    """Returns an async handler closing over `forecast_model_client`,
    matching the Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, metric: str, horizon: str) -> dict[str, Any]:
        return await forecast_model_client.run_forecast(metric, horizon)

    return handler
