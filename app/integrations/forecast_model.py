"""Placeholder forecasting model client.

This is intentionally mocked. No real forecasting model/provider has been
chosen yet — auth mechanism, request shape, and response schema are all
unknown at the time this was written, so nothing here should be treated as
a real integration. `run_forecast` is a TODO that returns realistic-shaped
placeholder output (a short predicted time series + confidence interval)
so the Forecast agent and the rest of the orchestrator can be built and
tested against a stable interface now.

When a real forecasting model is chosen, wire it in here — e.g.:
  - an httpx.AsyncClient (or the provider's SDK) configured with auth
  - proper error handling / retries for the actual model's failure modes
  - request/response models matching the real model's payloads
No other module should need to change; they only depend on this class's
method signature.
"""
import hashlib
from typing import Any


def _deterministic_base_value(metric: str) -> float:
    """Stable per-metric base value so the same metric always forecasts
    the same placeholder numbers (keeps tests reproducible)."""
    digest = hashlib.sha256(metric.encode()).hexdigest()
    return float(1000 + (int(digest[:8], 16) % 9000))


class ForecastModelClient:
    def __init__(self):
        # TODO(forecast-model): load real forecasting model base
        # URL/credentials from Settings once a provider is chosen (e.g.
        # FORECAST_MODEL_BASE_URL, FORECAST_MODEL_API_KEY).
        pass

    async def run_forecast(self, metric: str, horizon: str) -> dict[str, Any]:
        """TODO: replace with a real forecasting model call. Provider not
        yet chosen; do not treat this as real.

        Returns a realistic-shaped placeholder: 4 predicted periods with a
        modest deterministic growth trend, each with a +/-10% confidence
        interval — the same shape a real model response would have.
        """
        base = _deterministic_base_value(metric)
        predicted_values = [round(base * (1 + 0.03 * i), 2) for i in range(4)]
        confidence_interval = [
            {"period": i + 1, "low": round(v * 0.9, 2), "high": round(v * 1.1, 2)}
            for i, v in enumerate(predicted_values)
        ]
        return {
            "metric": metric,
            "horizon": horizon,
            "predicted_values": predicted_values,
            "confidence_interval": confidence_interval,
            "mock": True,
        }
