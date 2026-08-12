"""send_email tool — ToolSchema-conformant wrapper around
EmailClient.send_email (mocked; see app/integrations/email_client.py — no
real email-sending integration is wired up).

requires_confirmation=True: the Dispatcher refuses to run this via a
direct dispatch() call (see app/core/dispatcher.py) — it only ever
executes via dispatch_confirmed(), called only by the validated
POST /actions/{action_id}/confirm flow after the caller has seen and
confirmed the full draft (see app/agents/mail_agent.py).
"""
from typing import Any

from app.integrations.email_client import EmailClient
from app.models.tool_schema import ToolSchema

SEND_EMAIL_SCHEMA = ToolSchema(
    name="send_email",
    description="Send an email via EZOFIS's mail integration.",
    parameters={
        "type": "object",
        "properties": {
            "recipient": {"type": "string", "description": "The recipient's email address."},
            "subject": {"type": "string", "description": "The email subject line."},
            "body": {"type": "string", "description": "The email body."},
        },
        "required": ["recipient", "subject", "body"],
    },
    requires_confirmation=True,
)


def make_send_email_handler(email_client: EmailClient):
    """Returns an async handler closing over `email_client`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, recipient: str, subject: str, body: str) -> dict[str, Any]:
        return await email_client.send_email(recipient=recipient, subject=subject, body=body)

    return handler
