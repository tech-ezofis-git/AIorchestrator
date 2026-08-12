"""Placeholder email-sending client.

This is intentionally mocked and MUST remain mocked in this phase — no
real email-sending integration (Gmail API, Outlook API, SMTP credentials,
OAuth) is wired up, and `send_email` never attempts a real send under any
configuration. It only logs what would be sent (recipient + subject
metadata only — NEVER the body, see the method docstring) and returns a
mock confirmation.

This is also the tool the Dispatcher's confirmation gate protects (see
app/core/dispatcher.py): send_email's ToolSchema sets
requires_confirmation=True, so this method can only ever run via
Dispatcher.dispatch_confirmed(), invoked only by the validated
POST /actions/{action_id}/confirm flow — never a direct dispatch() call,
and never before the caller has seen and confirmed the full draft.

When a real email provider is chosen, wire it in here — e.g.:
  - an httpx.AsyncClient (or the provider's SDK) configured with OAuth/API
    key auth
  - proper error handling / retries for the actual provider's failure
    modes
No other module should need to change; they only depend on this class's
method signature.
"""
import logging
from typing import Any

logger = logging.getLogger("orchestrator.email_client")


class EmailClient:
    def __init__(self):
        # TODO(email-provider): load real email provider base URL/
        # credentials from Settings once a provider is chosen (e.g.
        # EMAIL_PROVIDER_BASE_URL, EMAIL_PROVIDER_API_KEY / OAuth config).
        pass

    async def send_email(self, *, recipient: str, subject: str, body: str) -> dict[str, Any]:
        """TODO: replace with a real send. Provider not yet chosen; this
        must never attempt a real send under any configuration until one
        is deliberately wired in here.

        Only the recipient and subject are logged — never the body. An
        email body is exactly the kind of "complete sensitive user
        content" the app's logging rule exists to protect.
        """
        logger.info(
            "email_send_mock",
            extra={"recipient": recipient, "subject": subject, "outcome": "sent_mock"},
        )
        return {
            "recipient": recipient,
            "subject": subject,
            "status": "sent_mock",
            "mock": True,
        }
