"""The Mail agent (Phase 3d) — the only agent with a real side effect
(sending email), which is exactly why it's the most heavily gated of all
of them. Layered safety, so no single layer has to be perfect:
  1. Narrow, action-verb-based intent triggers (app/core/intent_router.py)
     — not a broad "mail" substring match.
  2. Fail-closed recipient extraction (this file) — a validly-formatted
     email address or nothing happens.
  3. An architectural confirm-before-send gate at the Dispatcher level
     (app/core/dispatcher.py + app/core/pending_actions.py + the
     POST /actions/{action_id}/confirm endpoint in app/main.py) — even a
     confident recipient match only ever produces a *draft*.

MailAgent never calls Dispatcher.dispatch() for send_email directly — it
CAN'T; the Dispatcher rejects it, since send_email.requires_confirmation
is True. It only ever creates a pending action; the actual send happens
later, only via PendingActionStore + the confirm endpoint, only if the
caller explicitly confirms.
"""
import logging
import re
from typing import Optional

from app.core.pending_actions import PendingActionStore
from app.core.response_composer import ResponseComposer

logger = logging.getLogger("orchestrator.mail_agent")

# A practical, conservative "does this look like a real email address"
# check — not a full RFC 5322 implementation (that grammar is far more
# permissive than most real addresses need). Good enough to fail closed
# on anything that clearly isn't an address, which is the actual goal.
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_CLARIFICATION_REPLY = (
    "I couldn't find a valid email address in your message. Please "
    "include the recipient's full email address (e.g. 'jane@example.com') "
    "so I can draft the email."
)


def extract_recipient(message: str) -> Optional[str]:
    """Fail-closed recipient extraction: only returns a value when the
    message contains something matching _EMAIL_PATTERN. Returns None —
    never a guess — otherwise, with no fallback-to-whole-message path.
    Same discipline as ApAgent's invoice reference extraction, for the
    same reason: a wrong guess here means drafting (and, if confirmed,
    sending) an email to the wrong person.
    """
    match = _EMAIL_PATTERN.search(message)
    return match.group(0) if match else None


class MailAgent:
    def __init__(self, pending_action_store: PendingActionStore, response_composer: ResponseComposer):
        self._pending_actions = pending_action_store
        self._response_composer = response_composer

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]]) -> dict:
        """Returns {"reply": str, "usage": dict | None, "mail_draft": dict | None}.

        If no valid recipient is found: returns the clarification reply
        directly. No LLM call is made, no pending action is created,
        nothing reaches Redis. `mail_draft` is None.

        If a valid recipient is found: drafts subject+body via the LLM,
        creates a pending action, and returns the full draft (recipient,
        subject, body, action_id) — the caller must see this before
        confirming; NOTHING is sent yet, and nothing in this class can
        send it (see module docstring).

        Only the recipient, subject, action id, and outcome are logged —
        never the drafted body.
        """
        recipient = extract_recipient(message)
        if recipient is None:
            logger.info("mail_recipient_not_found", extra={"session_id": session_id, "outcome": "not_found"})
            return {"reply": _CLARIFICATION_REPLY, "usage": None, "mail_draft": None}

        synthesis = await self._response_composer.synthesize_mail_draft(recipient=recipient, request=message)
        subject = synthesis["subject"]
        body = synthesis["body"]

        pending_action = await self._pending_actions.create(
            tool_name="send_email",
            arguments={"recipient": recipient, "subject": subject, "body": body},
            session_id=session_id,
        )

        logger.info(
            "mail_draft_created",
            extra={
                "action_id": pending_action.action_id,
                "recipient": recipient,
                "subject": subject,
                "outcome": "drafted",
            },
        )

        reply = (
            f"Draft ready for {recipient} (action_id={pending_action.action_id}).\n"
            f"Subject: {subject}\n\n{body}\n\n"
            f"This has NOT been sent. POST /actions/{pending_action.action_id}/confirm to send it."
        )
        return {
            "reply": reply,
            "usage": synthesis["usage"],
            "mail_draft": {
                "action_id": pending_action.action_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
            },
        }
