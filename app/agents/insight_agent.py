"""The Insight agent (Phase 3a) — fetches report data through the
Dispatcher (never calling ezofis_client directly), then asks the Response
Composer's insight synthesis path for cited insights. Read-only,
single-turn. Same shape as SummaryAgent, using fetch_report_data instead
of fetch_document and an insight-focused synthesis prompt.
"""
from app.agents.reference_extraction import extract_reference
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer


class InsightAgent:
    def __init__(self, dispatcher: Dispatcher, response_composer: ResponseComposer):
        self._dispatcher = dispatcher
        self._response_composer = response_composer

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]], **_: object) -> dict:
        """Returns {"reply": str, "usage": dict | None, "cited_data_points": list[str]}.

        `cited_data_points` is every data point label the LLM was shown —
        same convention Search uses for `chunk_ids` (all retrieved chunks,
        not a parse of which ones the model's prose actually names).
        """
        report_id = extract_reference(message)
        report = await self._dispatcher.dispatch("fetch_report_data", {"report_id": report_id})
        synthesis = await self._response_composer.synthesize_insight(report=report)
        data_points = report.get("data_points") or []
        cited_data_points = [dp.get("label") for dp in data_points if dp.get("label")]
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "cited_data_points": cited_data_points,
        }
