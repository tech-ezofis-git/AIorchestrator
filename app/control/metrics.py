"""Prometheus metrics (Phase 5d) — in-process counters/histograms exposed
via `GET /metrics` (app/main.py), for aggregate operational visibility:
request counts/latencies by intent + status, LLM token usage, cache
hit/miss rates (Phase 5b), and guardrail rejection rates (Phase 4a).

Distinct from `audit_log` (Phase 4b), not a duplicate of it: `audit_log` is
the durable, per-request compliance record (one row per request, retained
in Postgres, sometimes holding a PII-redacted content snippet). `/metrics`
is pure AGGREGATE counts/rates — no request/response content, no
correlation ids, no session ids, nothing that could identify a specific
request, ever (rule 5). This module is the ONLY place metric label values
are chosen; every call site below passes a small, fixed vocabulary (an
intent name, an HTTP status code, a cache kind, a guardrail-rejection
reason) — never anything derived from user input or request identity. See
tests/test_metrics_no_identifier_leakage.py for the dedicated proof.

In-process only: metrics reset on restart. A real Prometheus server is
expected to poll `/metrics` periodically and retain history externally —
that's the standard Prometheus model, and exactly why nothing here is
persisted (rule 6). Alerting, dashboards, and trend storage are explicitly
out of scope (rule 8) — this repo exposes the interface only.

Recording is called from the SAME places that already produce a
structured log line for each event (rule/functional requirement) — not a
new code path:
  - `record_request` / `record_token_usage` — app/control/audit.py's
    `AuditMiddleware`, which already computes status_code/latency_ms and
    reads token_usage for its own "request completed" log line, for
    every request (success or failure), on every route.
  - `record_cache_event` — app/control/response_cache.py's `get()`,
    right alongside its existing `cache_hit`/`cache_miss` log lines.
  - `record_guardrail_rejection` — app/main.py's
    `http_exception_handler_with_audit`, right alongside the existing
    `_EVENT_TYPE_BY_STATUS_CODE` lookup it already uses to build the
    audit record's `event_type`.

Failure discipline: recording a metric must never fail or delay the
request it's instrumenting (rule 7) — every function below catches any
exception, logs a warning, and continues. `prometheus_client`'s in-memory
counters essentially can't fail, but the discipline is applied uniformly
with every other non-blocking instrumentation point in this app (audit
persistence in 4b, caching in 5b) rather than assumed away.
"""
import logging
from typing import Optional

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

logger = logging.getLogger("orchestrator.metrics")

# A dedicated registry, not prometheus_client's global default — keeps
# this module fully self-contained and independently testable (a fresh
# registry per test, see tests/test_metrics_endpoint.py) with no
# cross-test/cross-process state leaking through a shared singleton.
registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "orchestrator_requests_total",
    "Total requests, by intent and HTTP status code.",
    ["intent", "status_code"],
    registry=registry,
)

REQUEST_LATENCY_SECONDS = Histogram(
    "orchestrator_request_latency_seconds",
    "Request latency in seconds, by intent.",
    ["intent"],
    registry=registry,
)

LLM_TOKENS_TOTAL = Counter(
    "orchestrator_llm_tokens_total",
    "LLM token usage, by intent and token kind (prompt/completion).",
    ["intent", "kind"],
    registry=registry,
)

CACHE_EVENTS_TOTAL = Counter(
    "orchestrator_cache_events_total",
    "Response cache lookups (Phase 5b), by cache kind "
    "(embedding/search_result/forecast_narration) and outcome (hit/miss).",
    ["cache_kind", "outcome"],
    registry=registry,
)

GUARDRAIL_REJECTIONS_TOTAL = Counter(
    "orchestrator_guardrail_rejections_total",
    "Guardrail rejections (Phase 4a), by type.",
    ["reason"],
    registry=registry,
)

_UNKNOWN_INTENT = "unknown"

# Only these three are genuinely GUARDRAIL rejections (Phase 4a) — mirrors
# the 400/403/429 entries of app/main.py's _EVENT_TYPE_BY_STATUS_CODE.
# Other event types that flow through the same handler (action_not_found,
# upstream_error, service_unavailable, not_implemented, request_failed)
# are real outcomes too, but they're store/tool/upstream failures, not
# guardrail rejections — record_guardrail_rejection silently ignores them
# rather than making every call site filter first.
_GUARDRAIL_REJECTION_REASONS = frozenset({"content_filtered", "rate_limited", "permission_denied"})


def record_request(*, intent: Optional[str], status_code: int, latency_ms: float) -> None:
    """One request, success or failure. `intent` is whatever
    `request.state.intent` held by the time the response was produced —
    None (rendered as "unknown") for /health, /metrics, and any
    pre-classification guardrail rejection."""
    try:
        label = intent or _UNKNOWN_INTENT
        REQUEST_COUNT.labels(intent=label, status_code=str(status_code)).inc()
        REQUEST_LATENCY_SECONDS.labels(intent=label).observe(latency_ms / 1000)
    except Exception as exc:
        logger.warning("metrics_record_request_failed", extra={"error_type": type(exc).__name__})


def record_token_usage(*, intent: Optional[str], token_usage: Optional[dict]) -> None:
    """Token usage for one request, if any. A no-op when `token_usage` is
    None/empty (Chat/Search/Summary/Insight/Forecast/AP/Mail all pass one
    when an LLM call happened; OCR and pass-through/clarification replies
    never do)."""
    if not token_usage:
        return
    try:
        label = intent or _UNKNOWN_INTENT
        prompt_tokens = token_usage.get("prompt_tokens")
        completion_tokens = token_usage.get("completion_tokens")
        if prompt_tokens:
            LLM_TOKENS_TOTAL.labels(intent=label, kind="prompt").inc(prompt_tokens)
        if completion_tokens:
            LLM_TOKENS_TOTAL.labels(intent=label, kind="completion").inc(completion_tokens)
    except Exception as exc:
        logger.warning("metrics_record_token_usage_failed", extra={"error_type": type(exc).__name__})


def record_cache_event(*, cache_kind: str, hit: bool) -> None:
    """One cache lookup outcome. `cache_kind` is the ResponseCache
    `prefix` already in scope at every call site (embedding/
    search_result/forecast_narration) — never the cached key or value."""
    try:
        CACHE_EVENTS_TOTAL.labels(cache_kind=cache_kind, outcome="hit" if hit else "miss").inc()
    except Exception as exc:
        logger.warning("metrics_record_cache_event_failed", extra={"error_type": type(exc).__name__})


def record_guardrail_rejection(*, reason: str) -> None:
    """One guardrail rejection. Silently ignores any `reason` outside
    _GUARDRAIL_REJECTION_REASONS — see module docstring."""
    if reason not in _GUARDRAIL_REJECTION_REASONS:
        return
    try:
        GUARDRAIL_REJECTIONS_TOTAL.labels(reason=reason).inc()
    except Exception as exc:
        logger.warning("metrics_record_guardrail_rejection_failed", extra={"error_type": type(exc).__name__})


def render_latest() -> bytes:
    """Renders the current registry in Prometheus text-exposition format
    — called by `GET /metrics` (app/main.py). Consumable by any standard
    Prometheus-compatible scraper; nothing repo-specific about the wire
    format itself."""
    return generate_latest(registry)


__all__ = [
    "CONTENT_TYPE_LATEST",
    "record_request",
    "record_token_usage",
    "record_cache_event",
    "record_guardrail_rejection",
    "render_latest",
    "registry",
]
