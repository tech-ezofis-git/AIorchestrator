"""Durable audit log persistence (Phase 4b) — an additional sink alongside
the existing stdout structured logging (app/control/audit.py's
AuditMiddleware + configure_app_logging()), not a replacement.

INSERT-only by design: this module deliberately exposes no update/delete
path against `audit_log` — an audit trail that can be edited after the
fact isn't much of an audit trail. That's an application-level guarantee
(no code path here does anything but INSERT); it isn't enforced at the
database/permissions level in this phase — see the spec's explicit
out-of-scope list.

Writes are always best-effort and scheduled as a background task from
app/main.py, AFTER the user's response has already been prepared — a
Postgres outage here must never fail, delay, or retry-block the user's
actual request. See tests/test_audit_persistence_failure_does_not_fail_request.py
for proof, not just a claim.
"""
import logging
from typing import Any, Optional, Protocol

logger = logging.getLogger("orchestrator.audit_store")


class DBExecutor(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...


class AuditStore:
    def __init__(self, db: DBExecutor):
        self._db = db

    async def record(
        self,
        *,
        correlation_id: str,
        session_id: Optional[str],
        intent: Optional[str],
        event_type: str,
        status: str,
        latency_ms: Optional[float] = None,
        redacted_request_snippet: Optional[str] = None,
        redacted_response_snippet: Optional[str] = None,
    ) -> None:
        """Best-effort INSERT into audit_log. Any failure (Postgres down,
        pool exhausted, ...) is caught, logged as a warning (event/type
        only — never the exception's raw message, same discipline as
        every other typed-error boundary in this app), and swallowed —
        never re-raised, since this always runs after the user's response
        has already been prepared.
        """
        try:
            await self._db.execute(
                """
                INSERT INTO audit_log (
                    correlation_id, session_id, intent, event_type, status,
                    latency_ms, redacted_request_snippet, redacted_response_snippet
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                correlation_id,
                session_id,
                intent,
                event_type,
                status,
                latency_ms,
                redacted_request_snippet,
                redacted_response_snippet,
            )
        except Exception as exc:
            logger.warning(
                "audit_persistence_failed",
                extra={"correlation_id": correlation_id, "event_type": event_type, "error_type": type(exc).__name__},
            )
