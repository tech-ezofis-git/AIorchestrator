"""The Forecast agent (Phase 3b) — fetches a numeric forecast through the
Dispatcher's `run_forecast` tool (never calling the forecast model client
directly), then asks the Response Composer's forecast synthesis path to
narrate it into plain language. Unlike OCR, this DOES need a synthesis LLM
call — raw numeric predictions are genuine value-add to narrate, same
pattern as Search/Summary/Insight.

Phase 5b caches ONLY the narration (short TTL, app/control/
response_cache.py) — `run_forecast` itself is never cached, so
`forecast_result`'s raw numbers in the response are always freshly
computed on every call, cache hit or miss.

The narration cache key is built from the actual forecast dict's content
(a canonical, sorted-key JSON dump) rather than just the metric/horizon
phrasing extracted from the user's message. Deliberately: metric+horizon
happens to fully determine the mocked forecast client's output today
(app/integrations/forecast_model.py is a pure function of its inputs), but
that's a property of the mock, not something this cache should depend on.
Keying on the literal thing being narrated means that if/when a real,
time-varying forecasting model replaces the mock, a freshly-fetched
`forecast` result can never be paired with a narration that was cached for
different underlying numbers under the same key — the short TTL (not a
smarter invalidation scheme) is what bounds how long a narration can
outlive the data it was written for (rule 8).
"""
import json

from app.agents.reference_extraction import extract_metric_and_horizon
from app.control.response_cache import ResponseCache
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer

_NARRATION_CACHE_PREFIX = "forecast_narration"


class ForecastAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        response_composer: ResponseComposer,
        response_cache: ResponseCache,
        *,
        llm_model: str,
        narration_cache_ttl_seconds: int,
    ):
        self._dispatcher = dispatcher
        self._response_composer = response_composer
        self._cache = response_cache
        self._llm_model = llm_model
        self._narration_cache_ttl_seconds = narration_cache_ttl_seconds

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]], **_: object) -> dict:
        """Returns {"reply": str, "usage": dict | None, "forecast_result": dict}.

        `reply` is the narrated explanation (cache hit or miss, transparent
        either way); `forecast_result` is the raw, always-fresh tool output
        (metric, horizon, predicted_values, confidence_interval) for
        callers who want the numbers without the LLM's phrasing.
        """
        metric, horizon = extract_metric_and_horizon(message)
        forecast = await self._dispatcher.dispatch("run_forecast", {"metric": metric, "horizon": horizon})

        # Canonical, order-independent serialization of the exact thing
        # being narrated — see module docstring for why this is the input
        # text, not just `metric`/`horizon`.
        narration_input = json.dumps(forecast, sort_keys=True)

        synthesis = await self._cache.get(
            prefix=_NARRATION_CACHE_PREFIX, model=self._llm_model, input_text=narration_input
        )
        if synthesis is None:
            synthesis = await self._response_composer.synthesize_forecast(forecast=forecast)
            await self._cache.set(
                prefix=_NARRATION_CACHE_PREFIX,
                model=self._llm_model,
                input_text=narration_input,
                value=synthesis,
                ttl_seconds=self._narration_cache_ttl_seconds,
            )

        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "forecast_result": forecast,
        }
