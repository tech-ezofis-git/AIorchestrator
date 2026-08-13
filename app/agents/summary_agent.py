"""The Summary agent (Phase 3a) — fetches a document through the
Dispatcher (never calling ezofis_client directly — that's the point of the
ToolSchema/Dispatcher contract), then asks the Response Composer's summary
synthesis path for a concise summary. Read-only, single-turn.
"""
from app.agents.reference_extraction import extract_reference
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer


class SummaryAgent:
    def __init__(self, dispatcher: Dispatcher, response_composer: ResponseComposer):
        self._dispatcher = dispatcher
        self._response_composer = response_composer

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]], **_: object) -> dict:
        """Returns {"reply": str, "usage": dict | None, "document_id": str}.

        `history` isn't used — Phase 3a Summary is single-turn (summarize
        the document referenced in the current message). Accepted for
        interface parity with ChatAgent/SearchAgent.handle.
        """
        document_id = extract_reference(message)
        document = await self._dispatcher.dispatch("fetch_document", {"document_id": document_id})
        synthesis = await self._response_composer.synthesize_summary(document=document)
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "document_id": document_id,
        }
