"""Shared helpers for pulling structured references out of free-text chat
messages — SummaryAgent/InsightAgent need a document/report id, OcrAgent
needs a document/image reference, ForecastAgent needs a metric + horizon,
ApAgent needs an invoice reference. ChatRequest has no structured field
for any of these, so this parses the message instead. Good enough while
every underlying lookup (EZOFIS, the OCR engine, the forecast model) is
mocked and any reference "works"; a real implementation would more likely
take a structured field than parse free text.

`extract_invoice_reference` is deliberately NOT built on the same
loose "last id-shaped token" heuristic as the rest of this module — see
its docstring.
"""
import re
from typing import Optional

_ID_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")

# Short connector words to skip when picking the "last meaningful token" —
# NOT intent-trigger verbs (those stay eligible; a bare "summarize" or
# "forecast" is a valid, if minimal, fallback reference).
_STOPWORDS = {"for", "the", "on", "to", "of", "in", "and", "my", "is", "at", "me", "an", "a", "from", "this", "that", "with"}

_HORIZON_PATTERN = re.compile(
    r"next\s+\d+\s+(?:day|days|week|weeks|month|months|quarter|quarters|year|years)"
    r"|next\s+(?:day|week|month|quarter|year)"
    r"|q[1-4]\s*\d{4}"
    r"|q[1-4]",
    re.IGNORECASE,
)


def extract_reference(message: str) -> str:
    """Pulls a document/report/OCR/metric reference out of free text: the
    last id-shaped token (letters/digits/dashes/underscores, 3+ chars),
    skipping short connector words, e.g. "summarize document DOC-123" ->
    "DOC-123", "forecast revenue" -> "revenue". Falls back to the whole
    (stripped) message if nothing id-shaped is present.
    """
    candidates = [t for t in _ID_TOKEN_PATTERN.findall(message) if t.lower() not in _STOPWORDS]
    if candidates:
        return candidates[-1]
    return message.strip() or "unknown"


def extract_horizon(message: str, *, default: str = "next quarter") -> str:
    """Pulls a forecast horizon phrase out of free text, e.g. "next 3
    months", "Q3", "next quarter". Falls back to `default` if nothing
    horizon-shaped is present."""
    match = _HORIZON_PATTERN.search(message.lower())
    return match.group(0) if match else default


def extract_metric_and_horizon(message: str, *, default_horizon: str = "next quarter") -> tuple[str, str]:
    """Convenience for ForecastAgent: extracts the horizon first, strips
    the matched phrase out of the message, then extracts the metric from
    what's left — so a trailing horizon phrase ("... for next quarter")
    doesn't get picked up as the metric.
    """
    match = _HORIZON_PATTERN.search(message.lower())
    horizon = match.group(0) if match else default_horizon
    remainder = message[: match.start()] + message[match.end() :] if match else message
    metric = extract_reference(remainder)
    return metric, horizon


# Expected invoice reference format: an "INV" prefix, optional dash, then
# 3+ digits (e.g. "INV-1234", "INV999"). Adjust this one pattern if the
# real EZOFIS invoice reference format turns out to differ — nothing else
# needs to change.
_INVOICE_REFERENCE_PATTERN = re.compile(r"\bINV-?\d{3,}\b", re.IGNORECASE)


def extract_invoice_reference(message: str) -> Optional[str]:
    """Conservative, fail-closed extraction for AP (financial data): only
    returns a reference when the message contains something matching
    _INVOICE_REFERENCE_PATTERN. Returns None — never a guess — if no
    confident match is found; the caller (ApAgent) must treat None as
    "ask for clarification," not "use the whole message as a fallback."

    Deliberately NOT built on extract_reference's "last id-shaped token"
    heuristic (used by Summary/Insight/OCR/Forecast): a wrong guess here
    means surfacing financial data for the wrong invoice, not just an
    oddly-scoped answer, so there is no fallback-to-whole-message path.
    """
    match = _INVOICE_REFERENCE_PATTERN.search(message)
    if match is None:
        return None
    return match.group(0).upper()
