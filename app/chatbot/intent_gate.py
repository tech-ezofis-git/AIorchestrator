"""Lightweight Chatbot intent gate (CHATBOT.md-style) — no LLM, no DB."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateKind = Literal["greeting", "help", "out_of_scope", "database_question"]

_GREETING = (
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "howdy",
    "greetings",
)

_HELP = (
    "help",
    "what can you do",
    "what do you do",
    "how do you work",
    "how does this work",
    "capabilities",
    "commands",
)

# Clear non-tenant / non-document chatter — keep narrow to avoid blocking real queries.
_OUT_OF_SCOPE = (
    "weather",
    "tell me a joke",
    "who won the",
    "sports score",
    "recipe for",
    "write a poem",
    "play a game",
    "what's the capital",
    "what is the capital",
    "stock price",
    "horoscope",
    "lottery numbers",
)

_SCOPE_REPLY = (
    "That is outside what I can help with here. "
    "I can search this tenant (docs, repos, workflows, forms, comments, tickets) "
    "or propose actions (start workflow, upload, ticket+attachments, create user) "
    "after you confirm."
)

_HELP_REPLY = (
    "I search your tenant like Global Search, plus comments and tickets, "
    "and can propose confirm-gated actions: start workflow, upload to a repository, "
    "start ticket with attachments, or create a user."
)


@dataclass(frozen=True)
class GateDecision:
    kind: GateKind
    reply: str
    blocks: list[dict]


def classify_chatbot_intent(message: str) -> GateDecision:
    """Classify a Chatbot turn before any search tools run."""
    text = (message or "").strip()
    lowered = text.lower()
    if not lowered or lowered in {"help", "?"}:
        return GateDecision(
            kind="help",
            reply=_HELP_REPLY,
            blocks=[
                {
                    "type": "paragraph",
                    "text": (
                        "Ask me to find documents, repositories, workflows, forms, "
                        "comments, or tickets — or to start a workflow, upload a file, "
                        "open a ticket with attachments, or create a user. "
                        "Actions always require confirmation before Core runs."
                    ),
                },
                {
                    "type": "bullets",
                    "title": "Examples",
                    "items": [
                        "INV-2026-6001",
                        "What repositories are available?",
                        "find QUALITY CERTIFICATE",
                        "REQ-9001",
                        "search comments for approved",
                        "start that workflow",
                        "upload this to Accounts Payable repo",
                        "start ticket with attachments",
                        "create user jane@example.com Jane Doe",
                    ],
                },
            ],
        )

    if any(lowered == g or lowered.startswith(g + " ") or lowered.startswith(g + "!") for g in _GREETING) or lowered in {
        "hi!",
        "hello!",
        "hey!",
    }:
        return GateDecision(
            kind="greeting",
            reply=(
                "Hello! Ask me about documents, tickets, or comments — "
                "or say create user / start workflow when you are ready to act."
            ),
            blocks=[
                {
                    "type": "paragraph",
                    "text": (
                        "Hello! Ask me about documents, tickets, or comments — "
                        "or say create user / start workflow when you are ready to act."
                    ),
                }
            ],
        )

    if any(phrase in lowered for phrase in _HELP) and len(lowered) < 80:
        return GateDecision(
            kind="help",
            reply=_HELP_REPLY,
            blocks=[
                {
                    "type": "paragraph",
                    "text": _HELP_REPLY,
                },
                {
                    "type": "bullets",
                    "title": "Tips",
                    "items": [
                        "Pass tenantId (required)",
                        "Optional specificId locks one repository",
                        "query / message aliases all work",
                        "Tickets include inbox/transaction/instance text",
                        "Actions: NL or payload.propose_action → Confirm → POST /actions/{id}/confirm",
                        "Passwords never appear in pending-action UI (redacted)",
                        "Out-of-scope chatter (weather, jokes, …) is refused without searching",
                    ],
                },
            ],
        )

    if any(phrase in lowered for phrase in _OUT_OF_SCOPE):
        return GateDecision(
            kind="out_of_scope",
            reply=_SCOPE_REPLY,
            blocks=[
                {
                    "type": "paragraph",
                    "text": _SCOPE_REPLY,
                }
            ],
        )

    return GateDecision(kind="database_question", reply="", blocks=[])
