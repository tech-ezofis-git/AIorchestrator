"""Formats agent output into the final API response shape, and synthesizes
cited answers/summaries/insights/forecast-narrations/AP-status-explanations/
mail-drafts/extracted-memory-facts for the agents that need a second LLM
call (Search, Summary, Insight, Forecast, AP, Mail, and — new in Phase
5a — Chat's memory-write branch only). OCR (Phase 3b) and Chat's normal
(non-memory-write) path deliberately have no synthesis method here —
they're pass-through, so they never reach this class's LLM path.

`compose_chat_response` is the Phase 1 pass-through path — unchanged, still
just formatting, no LLM call. Every intent uses it to build the final
response envelope (correlation_id/latency/token_usage/chunk_ids/
document_id/cited_data_points/ocr_result/forecast_result/invoice_reference/
mail_draft).

`synthesize_search_answer` (Phase 2), `synthesize_summary`,
`synthesize_insight` (Phase 3a), `synthesize_forecast`, and
`synthesize_ap_status` (Phase 3c) share one private helper
(`_llm_synthesize`) for the "build messages, call the LLM, shape the
result" tail they all have in common — each keeps its own prompt and its
own pre-LLM shortcut where one applies, so `synthesize_search_answer`'s
external behavior (signature, prompt, returned shape) is unchanged from
Phase 2. `synthesize_mail_draft` (Phase 3d) does NOT use `_llm_synthesize`
— it needs two separate fields (subject, body) parsed out of the LLM's
response, not one free-form `content` string, so it calls the LLM
adapter directly and parses the result instead. Chat's reply is already
final by the time it reaches this class either way, so `chat` (and `ocr`)
behavior is unaffected by any of this.
"""
import re
from typing import Optional

from app.llm.adapter import LLMAdapter
from app.models.chat import ChatResponse, TokenUsage

_SEARCH_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Answer the user's question using ONLY the numbered "
    "excerpts below. Cite the excerpt(s) you used inline as [1], [2], etc. "
    "If the excerpts don't contain the answer, say so plainly instead of "
    "guessing."
)

_SUMMARY_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Write a concise summary of the document below. "
    "Stick to what's actually in the document — don't invent details it "
    "doesn't contain."
)

_INSIGHT_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Given the report data points below, surface the "
    "key insights — trends, outliers, notable numbers. Cite the specific "
    "data point label(s) behind each insight you state, and don't invent "
    "numbers not present in the data."
)

_FORECAST_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Given the raw forecast data below (predicted "
    "values and confidence intervals per period), explain it in plain "
    "language — the overall trend, and how much uncertainty the "
    "confidence interval implies. Don't invent numbers not present in "
    "the data."
)

_AP_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Given the invoice status data below, answer the "
    "user's question in plain language. Cite the invoice reference and "
    "the key fields (status, amount, relevant dates) explicitly. Don't "
    "invent details not present in the data — this is financial "
    "information and accuracy matters."
)

_MAIL_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Draft a professional, concise email for the "
    "recipient below based on the user's request. Respond in EXACTLY "
    "this format and nothing else — no preamble, no extra commentary:\n"
    "Subject: <subject line>\n"
    "Body:\n"
    "<email body>"
)

_MAIL_DRAFT_PATTERN = re.compile(r"Subject:\s*(.+?)\n+Body:\s*\n?(.*)", re.DOTALL | re.IGNORECASE)

_MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. The user has asked you to remember something. "
    "Extract a single, clean, storable fact from their message below — "
    "concise, third person, no conversational filler (e.g. \"remember "
    "that I prefer email over phone calls\" -> \"Prefers email over "
    "phone calls.\"). Respond with ONLY the extracted fact, nothing else."
)


class ResponseComposer:
    def __init__(self, llm_adapter: LLMAdapter):
        self._llm = llm_adapter

    def compose_chat_response(
        self,
        *,
        session_id: str,
        reply: str,
        correlation_id: str,
        latency_ms: float,
        token_usage: Optional[dict] = None,
        chunk_ids: Optional[list[str]] = None,
        document_id: Optional[str] = None,
        cited_data_points: Optional[list[str]] = None,
        ocr_result: Optional[dict] = None,
        forecast_result: Optional[dict] = None,
        invoice_reference: Optional[str] = None,
        mail_draft: Optional[dict] = None,
    ) -> ChatResponse:
        return ChatResponse(
            session_id=session_id,
            reply=reply,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            token_usage=TokenUsage(**token_usage) if token_usage else None,
            chunk_ids=chunk_ids,
            document_id=document_id,
            cited_data_points=cited_data_points,
            ocr_result=ocr_result,
            forecast_result=forecast_result,
            invoice_reference=invoice_reference,
            mail_draft=mail_draft,
        )

    async def _llm_synthesize(self, *, system_prompt: str, user_content: str) -> dict:
        """Shared tail for every synthesis path: build a 2-message prompt,
        call the LLM, shape the result. Returns {"content": str, "usage": dict | None}."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        result = await self._llm.chat_completion(messages)
        return {"content": result["content"], "usage": result["usage"]}

    async def synthesize_search_answer(self, *, question: str, results: list) -> dict:
        """One LLM call: numbered chunks + the user's question -> a cited
        answer. Returns {"content": str, "usage": dict | None}.

        `results` is a list of app.models.document.ScoredChunk — typed
        loosely here to avoid a hard import-time dependency on the
        knowledge package from this generic-formatting module.
        """
        if not results:
            return {
                "content": "I couldn't find anything relevant to that in the indexed documents.",
                "usage": None,
            }

        excerpts = "\n\n".join(
            f"[{i + 1}] (chunk_id={r.chunk.id})\n{r.chunk.text}" for i, r in enumerate(results)
        )
        return await self._llm_synthesize(
            system_prompt=_SEARCH_SYSTEM_PROMPT,
            user_content=f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}",
        )

    async def synthesize_summary(self, *, document: dict) -> dict:
        """One LLM call: fetched document content -> a concise summary.
        Returns {"content": str, "usage": dict | None}.

        `document` is the dict returned by the `fetch_document` tool
        (see app/tools/fetch_document.py) — typed loosely for the same
        reason as `results` above.
        """
        content = (document.get("content") or "").strip()
        title = document.get("title") or document.get("document_id") or "the document"
        if not content:
            return {"content": f"'{title}' has no content available to summarize.", "usage": None}

        return await self._llm_synthesize(
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            user_content=f"Document: {title}\n\n{content}",
        )

    async def synthesize_insight(self, *, report: dict) -> dict:
        """One LLM call: fetched report data points -> cited insights.
        Returns {"content": str, "usage": dict | None}.

        `report` is the dict returned by the `fetch_report_data` tool
        (see app/tools/fetch_report_data.py).
        """
        data_points = report.get("data_points") or []
        if not data_points:
            return {"content": "No data points were available to analyze.", "usage": None}

        formatted = "\n".join(f"- {dp.get('label')}: {dp.get('value')}" for dp in data_points)
        return await self._llm_synthesize(
            system_prompt=_INSIGHT_SYSTEM_PROMPT,
            user_content=f"Report: {report.get('report_id', 'unknown')}\n\nData points:\n{formatted}",
        )

    async def synthesize_forecast(self, *, forecast: dict) -> dict:
        """One LLM call: raw forecast numbers -> a plain-language
        narration. Returns {"content": str, "usage": dict | None}.

        `forecast` is the dict returned by the `run_forecast` tool (see
        app/tools/run_forecast.py). The raw numbers are also returned
        as-is to the caller alongside this narration (see ForecastAgent),
        so users who want just the numbers aren't forced through the
        LLM's phrasing.
        """
        predicted_values = forecast.get("predicted_values") or []
        if not predicted_values:
            return {"content": "No forecast data was available to narrate.", "usage": None}

        confidence_interval = forecast.get("confidence_interval") or []
        interval_by_period = {ci.get("period"): ci for ci in confidence_interval}
        lines = []
        for i, value in enumerate(predicted_values, start=1):
            ci = interval_by_period.get(i)
            if ci:
                lines.append(f"- Period {i}: {value} (range: {ci.get('low')}-{ci.get('high')})")
            else:
                lines.append(f"- Period {i}: {value}")
        formatted = "\n".join(lines)

        return await self._llm_synthesize(
            system_prompt=_FORECAST_SYSTEM_PROMPT,
            user_content=(
                f"Metric: {forecast.get('metric', 'unknown')}\n"
                f"Horizon: {forecast.get('horizon', 'unknown')}\n\n"
                f"Predicted values:\n{formatted}"
            ),
        )

    async def synthesize_ap_status(self, *, invoice: dict) -> dict:
        """One LLM call: fetched invoice status data -> a plain-language
        answer citing the invoice reference and key fields. Returns
        {"content": str, "usage": dict | None}.

        `invoice` is the dict returned by the `fetch_invoice_status` tool
        (see app/tools/fetch_invoice_status.py). No pre-LLM shortcut is
        needed here — ApAgent only calls this after a confident reference
        was found and the tool call succeeded, so `invoice` always has
        content.
        """
        reference = invoice.get("invoice_reference", "unknown")
        fields = "\n".join(
            f"- {key}: {value}" for key, value in invoice.items() if key not in ("invoice_reference", "mock")
        )
        return await self._llm_synthesize(
            system_prompt=_AP_SYSTEM_PROMPT,
            user_content=f"Invoice reference: {reference}\n\nData:\n{fields}",
        )

    async def synthesize_mail_draft(self, *, recipient: str, request: str) -> dict:
        """One LLM call: recipient + the user's request -> a drafted
        subject and body. Returns
        {"subject": str, "body": str, "usage": dict | None}.

        Unlike every other synthesize_* method, this parses the LLM's raw
        text into two fields (the model is asked to follow a strict
        "Subject: ... / Body: ..." format) rather than returning free-form
        content as-is — Mail needs both fields separately, for the draft
        response the caller must review and for send_email's arguments
        once confirmed. If the model doesn't follow the format, this
        falls back to a generic subject + the raw text as the body,
        rather than silently dropping the draft.
        """
        result = await self._llm.chat_completion(
            [
                {"role": "system", "content": _MAIL_SYSTEM_PROMPT},
                {"role": "user", "content": f"Recipient: {recipient}\n\nRequest: {request}"},
            ]
        )
        subject, body = _parse_mail_draft(result["content"], fallback_subject=f"Message for {recipient}")
        return {"subject": subject, "body": body, "usage": result["usage"]}

    async def synthesize_memory_fact(self, *, instruction: str) -> dict:
        """One LLM call: a free-form "remember that ..." instruction ->
        a clean, storable fact. Returns {"content": str, "usage": dict | None}.

        This is the one Chat-path call that genuinely earns its cost —
        see app/agents/chat_agent.py's module docstring. No pre-LLM
        shortcut is needed: ChatAgent only calls this once a memory-write
        trigger phrase has already matched, so `instruction` is always
        non-empty.
        """
        return await self._llm_synthesize(
            system_prompt=_MEMORY_EXTRACTION_SYSTEM_PROMPT,
            user_content=instruction,
        )


def _parse_mail_draft(content: str, *, fallback_subject: str) -> tuple[str, str]:
    """Parses the "Subject: ...\\nBody:\\n..." format synthesize_mail_draft
    asks the LLM for. Falls back to a generic subject + the raw content as
    the body if the model didn't follow the format exactly — drafting a
    slightly-off-format email is fine; silently dropping the draft isn't."""
    match = _MAIL_DRAFT_PATTERN.search(content)
    if match:
        subject = match.group(1).strip()
        body = match.group(2).strip()
        if subject and body:
            return subject, body
    return fallback_subject, content.strip()
