"""Correlation-ID middleware + structured logging for the whole app.

Every request gets a correlation ID (attached to request.state, echoed back
in the response header, and stashed in a contextvar). After the request
completes, one structured JSON record is written to stdout with correlation
ID, session ID (if the route set one), timestamp, latency, and token usage
(if the route set it).

`configure_app_logging()` additionally makes every other `orchestrator.*`
logger (llm adapter, context manager, ...) emit structured JSON too, and
auto-stamps those records with the current request's correlation_id via the
contextvar above — so a Redis/LLM failure log line can always be tied back
to the request that triggered it, not just the final audit line.

Phase 4 replaces the stdout sink with a real audit store; the record shapes
here are designed to translate directly.

Phase 5d: this is also the single point every request's Prometheus
request-count/latency/token-usage metrics are recorded from
(app/control/metrics.py) — it already computes status_code/latency_ms and
reads token_usage for the stdout record below, for every request
(success or failure) on every route, so it's the natural, DRY home for
that instrumentation rather than duplicating latency-tracking at each of
main.py's several response-producing call sites. Purely additive: nothing
about this middleware's existing behavior changes.
"""
import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.control.metrics import record_request, record_token_usage

# Readable by any orchestrator.* logger via _JsonLogFormatter, set by
# AuditMiddleware for the lifetime of a request.
correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)

logger = logging.getLogger("orchestrator.audit")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


_STANDARD_LOG_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class _JsonLogFormatter(logging.Formatter):
    """Renders a LogRecord as one JSON line: level, logger, event, the
    current request's correlation_id, and any `extra=` fields the call site
    passed (e.g. session_id, model, error_type). Never includes exception
    text/args verbatim, so a caught exception's message (which can contain
    provider/auth error details) can't leak just by being logged."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None) or correlation_id_ctx.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_app_logging(level: str = "INFO") -> None:
    """Attach one JSON stdout handler to the 'orchestrator' logger namespace
    so every orchestrator.* component (llm, context_manager, app, ...) logs
    structured JSON with a correlation_id — not only the per-request audit
    record `AuditMiddleware` emits below. Idempotent — safe to call more
    than once (e.g. once per test)."""
    app_logger = logging.getLogger("orchestrator")
    app_logger.setLevel(level.upper())
    if not any(isinstance(h.formatter, _JsonLogFormatter) for h in app_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonLogFormatter())
        app_logger.addHandler(handler)
    # orchestrator.audit manages its own handler/format below and already
    # sets propagate=False on itself; this stops the *other* orchestrator.*
    # loggers (llm, context_manager, app) from also hitting uvicorn's root
    # handler and printing twice.
    app_logger.propagate = False


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        # Routes may set these on request.state to enrich the audit record.
        request.state.session_id = None
        request.state.token_usage = None

        token = correlation_id_ctx.set(correlation_id)
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            correlation_id_ctx.reset(token)
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

        response.headers["X-Correlation-ID"] = correlation_id

        # Routes set request.state.intent once classified (None for
        # /health, /metrics, and pre-classification guardrail rejections)
        # — read AFTER call_next so this reflects the route's final value,
        # not whatever was there (nothing) before the route ran.
        intent = getattr(request.state, "intent", None)
        token_usage = getattr(request.state, "token_usage", None)

        record_request(intent=intent, status_code=response.status_code, latency_ms=latency_ms)
        record_token_usage(intent=intent, token_usage=token_usage)

        record = {
            "correlation_id": correlation_id,
            "session_id": getattr(request.state, "session_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
        }
        logger.info(json.dumps(record))

        return response
