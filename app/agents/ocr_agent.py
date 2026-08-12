"""The OCR agent (Phase 3b) — extracts text via the Dispatcher's `run_ocr`
tool (never calling the OCR engine client directly). Deliberately makes NO
synthesis LLM call: the extracted text doesn't need reformatting, and
adding an LLM call here would be pure unneeded cost — same reasoning that
kept Chat down to a single LLM call with no second pass in Phase 1.
"""
from app.agents.reference_extraction import extract_reference
from app.core.dispatcher import Dispatcher


class OcrAgent:
    def __init__(self, dispatcher: Dispatcher):
        self._dispatcher = dispatcher

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]]) -> dict:
        """Returns {"reply": str, "usage": None, "ocr_result": dict}.

        `usage` is always None — no LLM call is made. `reply` is the
        extracted text itself (pass-through); `ocr_result` carries the
        full structured tool output (text, confidence, source_reference)
        for callers that want the whole shape, not just the text.
        """
        reference = extract_reference(message)
        result = await self._dispatcher.dispatch("run_ocr", {"reference": reference})
        return {
            "reply": result["text"],
            "usage": None,
            "ocr_result": result,
        }
