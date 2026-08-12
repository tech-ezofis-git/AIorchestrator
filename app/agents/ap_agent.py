"""The AP (Accounts Payable) agent (Phase 3c) — fetches invoice status
through the Dispatcher's `fetch_invoice_status` tool (never calling
ezofis_client directly), then asks the Response Composer's AP synthesis
path for a plain-language answer. Read-only, single-turn.

Higher-stakes than prior agents: this touches financial data, so
reference extraction is deliberately conservative and fails closed (see
app.agents.reference_extraction.extract_invoice_reference). Only a
clearly-formatted invoice reference triggers a tool call; anything else
returns an explicit clarification — no tool call, no LLM call, no guess.
A wrong guess here would mean surfacing financial data for the wrong
invoice, not just an oddly-scoped answer.
"""
import logging

from app.agents.reference_extraction import extract_invoice_reference
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer

logger = logging.getLogger("orchestrator.ap_agent")

_CLARIFICATION_REPLY = (
    "I couldn't identify a specific invoice reference in your message. "
    "Please provide one (e.g. 'INV-1234') so I can look up its status."
)


class ApAgent:
    def __init__(self, dispatcher: Dispatcher, response_composer: ResponseComposer):
        self._dispatcher = dispatcher
        self._response_composer = response_composer

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]]) -> dict:
        """Returns {"reply": str, "usage": dict | None, "invoice_reference": str | None}.

        If no confident invoice reference is found, returns the
        clarification reply directly — the Dispatcher is never called (no
        tool call is made) and the LLM is never called either (this is a
        canned, deterministic refusal, not a guess dressed up by an LLM).

        Only the reference identifier and the outcome (found/not found)
        are logged — never the fetched invoice's financial fields. A tool
        failure is logged separately by the Dispatcher itself (tool_name +
        error_type only, also no payload).
        """
        invoice_reference = extract_invoice_reference(message)
        if invoice_reference is None:
            logger.info("ap_reference_not_found", extra={"session_id": session_id, "outcome": "not_found"})
            return {"reply": _CLARIFICATION_REPLY, "usage": None, "invoice_reference": None}

        logger.info(
            "ap_reference_identified",
            extra={"invoice_reference": invoice_reference, "outcome": "found"},
        )
        invoice = await self._dispatcher.dispatch(
            "fetch_invoice_status", {"invoice_reference": invoice_reference}
        )
        synthesis = await self._response_composer.synthesize_ap_status(invoice=invoice)
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "invoice_reference": invoice_reference,
        }
