"""Classifies an incoming message into an Intent.

Phase 1 always resolved to Intent.CHAT. Phase 2 added real (if simple)
`search` classification; Phase 3a added `summary`/`insight`; Phase 3b
added `ocr`/`forecast`; Phase 3c added `ap`; Phase 3d adds `mail`. All are
small sets of keyword/phrase triggers, checked in a fixed order (search,
summary, insight, ocr, forecast, ap, mail) so a message matching more than
one list resolves deterministically rather than by dict/set iteration
order. This is deliberately lightweight — no extra model call, no new env
var — swapping in smarter classification later (embeddings, a small
model, ...) is a one-function change behind the same signature, same as
Phase 1 intended.

CAUTION (flagged per Phase 3b spec, not fixed yet): this substring-based
approach is fine while every registered intent is read-only — a
misclassification here means, at worst, the wrong read-only agent runs.
That stops being true once Mail (Phase 3d) is registered: a message that
should have been `chat` but gets misrouted to a send-capable agent is a
different order of problem. Revisit classification robustness (real
model-based classification, or at least an explicit confirmation step for
send-capable intents) *before* Mail lands — don't ship Mail behind this
heuristic as-is.

NOTE (Phase 3d): Mail is now registered — this classifier was NOT
rewritten to address the caution above; instead, three independent layers
were added around it so no single one has to be perfect: (1) `_MAIL_TRIGGERS`
below is deliberately narrow and action-verb-based (not a bare "mail"
substring match), (2) Mail Agent's recipient extraction fails closed — a
validly-formatted email address or nothing happens
(app/agents/mail_agent.py), and (3) an architectural confirm-before-send
gate at the Dispatcher level means even a correct classification + a
correct recipient match only ever produces a *draft* — `send_email` cannot
execute without a separate, explicit POST /actions/{action_id}/confirm
call after the caller has seen the full draft (app/core/dispatcher.py,
app/core/pending_actions.py). A misclassification here now costs, at
worst, an unwanted draft nobody confirms — not a sent email. The
underlying classifier itself is still this same lightweight substring
approach; revisit it for real classification if that turns out to matter
more than the layered mitigation covers.
"""
from enum import Enum
from typing import Any, Optional


class Intent(str, Enum):
    CHAT = "chat"
    SEARCH = "search"
    SUMMARY = "summary"
    INSIGHT = "insight"
    FORECAST = "forecast"
    OCR = "ocr"
    MAIL = "mail"
    AP = "ap"
    PROMPT = "prompt"


# Keyword/phrase triggers per intent. Checked as substrings of the
# lowercased message — simple and deterministic, not ML-based.
_SEARCH_TRIGGERS = (
    "search",
    "find",
    "look up",
    "lookup",
    "where is",
    "where can i find",
    "show me",
    "locate",
)

_SUMMARY_TRIGGERS = (
    "summarize",
    "summarise",
    "summary",
    "tl;dr",
    "tldr",
)

_INSIGHT_TRIGGERS = (
    "insight",
    "insights",
    "analyze",
    "analyse",
    "analysis",
)

_OCR_TRIGGERS = (
    "ocr",
    "extract text",
    "scan",
    "scanned",
    "read the image",
    "read this image",
)

_FORECAST_TRIGGERS = (
    "forecast",
    "predict",
    "prediction",
    "projection",
    "project the",
)

_AP_TRIGGERS = (
    "invoice",
    "accounts payable",
    "ap status",
    "payment status",
)

# Deliberately narrow and action-verb-based — NOT a bare "mail" substring
# match (that would false-positive on things like "check the mail room
# policy" or "what's my email address"). See the module docstring's NOTE
# on why this narrowness is only one of three independent safety layers
# for Mail, not the only one.
_MAIL_TRIGGERS = (
    "send an email",
    "send email",
    "send a mail",
    "compose an email",
    "compose email",
    "draft an email",
    "draft email",
    "write an email",
    "email to",
)


class IntentRouter:
    """Classifies free-text messages into one of the platform's Intents."""

    def __init__(self) -> None:
        self._custom: list[tuple[str, tuple[str, ...]]] = []

    def set_custom_agents(self, agents: list[dict[str, Any]]) -> None:
        """Refresh keyword triggers for catalog custom agents (checked after builtins)."""
        custom: list[tuple[str, tuple[str, ...]]] = []
        for agent in agents:
            slug = str(agent.get("slug") or "").strip()
            phrases = tuple(
                str(p).strip().lower()
                for p in (agent.get("trigger_phrases") or [])
                if p and str(p).strip()
            )
            if slug and phrases:
                custom.append((slug, phrases))
        self._custom = custom

    def match_custom_slug(self, message: str) -> Optional[str]:
        normalized = message.strip().lower()
        if not normalized:
            return None
        for slug, phrases in self._custom:
            if any(phrase in normalized for phrase in phrases):
                return slug
        return None

    async def classify(self, message: str) -> Intent:
        """Return the Intent for `message`.

        Checked in order: `search`, `summary`, `insight`, `ocr`,
        `forecast`, `ap`, `mail`; everything else resolves to `chat`.
        `prompt` is explicit-only (`intent: "prompt"`) so the word
        "prompt" never steals another job. No branch is a hardcoded
        bypass — a message genuinely has to match (or not match) each
        trigger set in turn. See the module docstring's CAUTION/NOTE
        before touching `_MAIL_TRIGGERS` or adding another send-capable
        intent.
        """
        normalized = message.strip().lower()
        if not normalized:
            return Intent.CHAT
        if any(trigger in normalized for trigger in _SEARCH_TRIGGERS):
            return Intent.SEARCH
        if any(trigger in normalized for trigger in _SUMMARY_TRIGGERS):
            return Intent.SUMMARY
        if any(trigger in normalized for trigger in _INSIGHT_TRIGGERS):
            return Intent.INSIGHT
        if any(trigger in normalized for trigger in _OCR_TRIGGERS):
            return Intent.OCR
        if any(trigger in normalized for trigger in _FORECAST_TRIGGERS):
            return Intent.FORECAST
        if any(trigger in normalized for trigger in _AP_TRIGGERS):
            return Intent.AP
        if any(trigger in normalized for trigger in _MAIL_TRIGGERS):
            return Intent.MAIL
        # Prompt is explicit-only. Unmatched free-text stays chat.
        return Intent.CHAT
