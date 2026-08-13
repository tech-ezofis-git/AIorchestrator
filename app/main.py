"""FastAPI app entrypoint — wires the Chat (Phase 1), Search (Phase 2),
Summary/Insight (Phase 3a), OCR/Forecast (Phase 3b), AP (Phase 3c), Mail
(Phase 3d), Guardrails (Phase 4a), durable Audit Storage (Phase 4b),
durable cross-session Chat Memory (Phase 5a), Response Caching (Phase 5b),
and Prometheus Monitoring (Phase 5d) pipelines together. (Phase 5c, the
eval harness, is a standalone tool under app/evals/ — it never touches
this file.)

Phase 5a is entirely internal to ChatAgent (app/agents/chat_agent.py) —
no route handler below changes. `store_memory`/`fetch_memories` are just
two more Dispatcher tools, registered here like every other tool, backed
by MemoryStore (app/control/memory_store.py) instead of a mocked
integration client since this one is genuinely Postgres-backed.

Phase 5b is likewise entirely internal to SearchAgent/ForecastAgent
(app/control/response_cache.py) — no route handler below changes, and no
new Dispatcher tool is involved (caching wraps the embedding/LLM calls
those two agents already make, not a tool call). See those two agents'
module docstrings for what's cached and why AP/Summary/Insight/Chat/OCR/
Mail deliberately aren't.

Phase 5d adds `GET /metrics` (Prometheus text-exposition format,
app/control/metrics.py) — deliberately NOT run through check_content/
RateLimiter/check_permission the way /chat and /actions/confirm are: it's
an operational endpoint, not a user-facing capability (rule 4), so it's
simply never given those calls, the same way /health never has been.
Metric recording itself happens at existing instrumentation points
elsewhere (AuditMiddleware for request count/latency/token usage,
ResponseCache for cache hit/miss, this file's HTTPException handler below
for guardrail rejections) — see app/control/metrics.py's docstring for
the full reasoning.

Request flow for POST /chat:
  ContentFilter (check_content) ->
  RateLimiter (check, keyed by session_id) ->
  ContextManager (load history) ->
  IntentRouter (classify chat/search/summary/insight/ocr/forecast/ap/mail) ->
  PermissionCheck (check_permission, per classified intent) ->
  AgentRouter -> ChatAgent | SearchAgent | SummaryAgent | InsightAgent |
                 OcrAgent | ForecastAgent | ApAgent | MailAgent ->
    LLMAdapter / HybridSearch /
    Dispatcher(fetch_document|fetch_report_data|run_ocr|run_forecast|
               fetch_invoice_status) / PendingActionStore (Mail only) ->
  ResponseComposer -> ContextManager (append turn)
AuditMiddleware wraps every request with a correlation ID + structured
stdout log. AuditStore (Phase 4b) additionally persists one row per
request to Postgres — see "Audit persistence" below.

Guardrail order is deliberately fail-fast-cheapest-first: the content
filter needs no I/O at all, the rate limiter needs one Redis round trip,
and the permission check needs to know the classified intent (so it can
only run after intent classification, not before) — see
app/control/content_filter.py, app/control/rate_limiter.py,
app/control/permissions.py. None of the three change any agent's
internals; they're a layer in front of the existing pipeline.

Mail never sends from /chat — it only ever creates a pending action.
POST /actions/{action_id}/confirm is the only path that can actually
execute send_email (via Dispatcher.dispatch_confirmed), and only after
looking up a real pending action — and the same three guardrails apply
there first too (content filter runs against `action_id` itself, since
confirm has no free-text message; permission uses a confirm-specific,
intent-agnostic check since the tool isn't known until after the pending
action is looked up, which happens only after the gates pass).

Audit persistence (Phase 4b): every request path — success, guardrail
rejection, or agent-level failure, on both endpoints — ends with a
best-effort audit_log row (app/control/audit_store.py), written as a
background task AFTER the response is already being sent, never
retried, never allowed to fail or delay the user's actual response (see
tests/test_audit_persistence_failure_does_not_fail_request.py). Two
mechanisms are used depending on how the response is produced:
  - On a normal `return`, FastAPI's `BackgroundTasks` dependency works as
    documented — the task runs after the response is sent.
  - On `raise HTTPException(...)` (almost every guardrail/error path in
    this file), a `BackgroundTasks` object added via the dependency is
    silently DROPPED — FastAPI's default exception handling builds an
    unrelated response that never sees it. So instead, a custom
    `@app.exception_handler(HTTPException)` below rebuilds the *exact*
    same response FastAPI's default would (identical body/status/headers
    — see that handler's docstring) and attaches the audit write there,
    reading whatever `request.state.*` fields were set before the raise
    (session_id, intent, raw_message — all optional/defaulted so this
    works even for pre-classification rejections).
Chat/Search/Summary/Insight/OCR/Forecast rows get a PII-redacted,
length-capped snippet of the request/response; AP/Mail rows (and any row
where intent isn't yet known, e.g. content-filter/rate-limit rejections)
never do — see `_snippet_for_audit` below and
app/control/pii_redaction.py.
"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import asyncpg
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from redis.asyncio import Redis
from starlette.background import BackgroundTask

from app.agents.ap_agent import ApAgent
from app.agents.chat_agent import ChatAgent
from app.agents.forecast_agent import ForecastAgent
from app.agents.insight_agent import InsightAgent
from app.agents.mail_agent import MailAgent
from app.agents.ocr_agent import OcrAgent
from app.agents.search_agent import SearchAgent
from app.agents.summary_agent import SummaryAgent
from app.config import get_settings
from app.control.audit import AuditMiddleware, configure_app_logging
from app.control.audit_store import AuditStore
from app.control.content_filter import ContentFilterRejectedError, check_content
from app.control.memory_store import MemoryStore
from app.control.metrics import CONTENT_TYPE_LATEST, record_guardrail_rejection, render_latest
from app.control.permissions import (
    MockPermissionProvider,
    PermissionDeniedError,
    check_confirm_permission,
    check_permission,
)
from app.control.pii_redaction import redact_and_cap
from app.control.response_cache import ResponseCache
from app.control.rate_limiter import RateLimitExceededError, RateLimiter, RateLimiterStoreUnavailableError
from app.core.agent_router import AgentRouter
from app.core.context_manager import ContextManager, SessionStoreUnavailableError
from app.core.dispatcher import Dispatcher, ToolExecutionError
from app.core.intent_router import Intent, IntentRouter
from app.core.pending_actions import PendingActionStore, PendingActionStoreUnavailableError
from app.core.response_composer import ResponseComposer
from app.integrations.email_client import EmailClient
from app.integrations.ezofis_client import EzofisClient
from app.integrations.forecast_model import ForecastModelClient
from app.integrations.ocr_engine import OcrEngineClient
from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.vector_store import VectorStore, VectorStoreUnavailableError
from app.llm.adapter import LLMAdapter, LLMAdapterError
from app.llm.embedding_adapter import EmbeddingAdapter, EmbeddingAdapterError
from app.llm.model_presets import (
    DEFAULT_PRESET_ID,
    apply_preset,
    get_preset,
    list_presets_public,
)
from app.llm.runtime_models import RuntimeModelSelection
from app.models.chat import ChatRequest, ChatResponse
from app.models.chat_request_parser import parse_chat_request
from app.models.pending_action import ConfirmActionResponse
from app.agents.ocr_helpers import InvalidOcrPageError, resolve_pageno
from app.tools.fetch_document import FETCH_DOCUMENT_SCHEMA, make_fetch_document_handler
from app.tools.fetch_invoice_status import FETCH_INVOICE_STATUS_SCHEMA, make_fetch_invoice_status_handler
from app.tools.fetch_memories import FETCH_MEMORIES_SCHEMA, make_fetch_memories_handler
from app.tools.fetch_report_data import FETCH_REPORT_DATA_SCHEMA, make_fetch_report_data_handler
from app.tools.run_forecast import RUN_FORECAST_SCHEMA, make_run_forecast_handler
from app.tools.run_ocr import RUN_OCR_SCHEMA, make_run_ocr_handler
from app.tools.send_email import SEND_EMAIL_SCHEMA, make_send_email_handler
from app.tools.store_memory import STORE_MEMORY_SCHEMA, make_store_memory_handler

logger = logging.getLogger("orchestrator.app")

# Manual test console (not a phase deliverable, just a dev convenience) —
# a themed static page under GET /console that calls this same origin's
# /chat and /actions/{id}/confirm directly via fetch(), so no CORS config
# is needed. Served from disk on every request rather than cached in
# memory so editing app/static/console.html takes effect without a
# restart (matches the docker-compose bind mount for ./app already used
# throughout local dev).
_STATIC_DIR = Path(__file__).parent / "static"
_CONSOLE_HTML_PATH = _STATIC_DIR / "console.html"

# Only these six intents ever get a request/response snippet persisted —
# AP and Mail keep their existing stricter no-content discipline (Phase
# 3c/3d), and anything where intent isn't classified yet (content filter,
# rate limit rejections) is conservatively treated the same as AP/Mail:
# we don't know what it would have been, so we don't snippet it.
_SNIPPETABLE_INTENTS = {"chat", "search", "summary", "insight", "ocr", "forecast"}

# Only send_email exists today; mapped explicitly rather than guessed so
# a future gated tool doesn't silently inherit the wrong intent label.
_TOOL_NAME_TO_INTENT = {"send_email": "mail"}

_EVENT_TYPE_BY_STATUS_CODE = {
    400: "content_filtered",
    403: "permission_denied",
    404: "action_not_found",
    429: "rate_limited",
    501: "not_implemented",
    502: "upstream_error",
    503: "service_unavailable",
}


def _status_bucket(status_code: int) -> str:
    if status_code < 300:
        return "success"
    if status_code in (400, 403, 404, 429):
        return "rejected"
    return "error"


def _snippet_for_audit(intent: Optional[str], text: Optional[str]) -> Optional[str]:
    """None unless `intent` is one of the six low-stakes intents AND
    `text` is present — conservative by construction: unknown intent
    (None) and AP/Mail both fall through to None, same as each other."""
    if text is None or intent not in _SNIPPETABLE_INTENTS:
        return None
    return redact_and_cap(text)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_app_logging(settings.log_level)

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    db_pool = await asyncpg.create_pool(settings.database_url)

    llm_adapter = LLMAdapter(settings)
    # Console can switch default/fallback at runtime; selection is persisted
    # in Redis so hosting restarts keep the last manual Save.
    runtime_models = RuntimeModelSelection(default_preset_id=DEFAULT_PRESET_ID)
    loaded_selection = await runtime_models.load_from_redis(redis_client)
    if not loaded_selection and runtime_models.fallback_preset_id is None:
        env_fallback = (settings.ocr_fallback_model or "").strip()
        if env_fallback and get_preset(env_fallback):
            runtime_models.fallback_preset_id = env_fallback
    if not settings.llm_api_base:
        apply_preset(llm_adapter, runtime_models.default_preset_id)
    embedding_adapter = EmbeddingAdapter(settings)
    ezofis_client = EzofisClient()
    context_manager = ContextManager(redis_client, settings.session_ttl_seconds, ezofis_client)
    intent_router = IntentRouter()
    response_composer = ResponseComposer(llm_adapter)
    response_cache = ResponseCache(redis_client)

    vector_store = VectorStore(db_pool)
    hybrid_search = HybridSearch(vector_store, embedding_adapter)
    search_agent = SearchAgent(
        hybrid_search,
        response_composer,
        embedding_adapter,
        response_cache,
        top_n=settings.search_top_n,
        embedding_model=settings.embedding_model,
        llm_model=settings.llm_model,
        embedding_cache_ttl_seconds=settings.embedding_cache_ttl_seconds,
        result_cache_ttl_seconds=settings.search_result_cache_ttl_seconds,
    )

    ocr_engine_client = OcrEngineClient(settings)
    forecast_model_client = ForecastModelClient()
    email_client = EmailClient()
    memory_store = MemoryStore(db_pool)

    permission_provider = MockPermissionProvider()

    dispatcher = Dispatcher()
    dispatcher.register_tool(FETCH_DOCUMENT_SCHEMA, make_fetch_document_handler(ezofis_client))
    dispatcher.register_tool(FETCH_REPORT_DATA_SCHEMA, make_fetch_report_data_handler(ezofis_client))
    dispatcher.register_tool(RUN_OCR_SCHEMA, make_run_ocr_handler(ocr_engine_client))
    dispatcher.register_tool(RUN_FORECAST_SCHEMA, make_run_forecast_handler(forecast_model_client))
    dispatcher.register_tool(FETCH_INVOICE_STATUS_SCHEMA, make_fetch_invoice_status_handler(ezofis_client))
    dispatcher.register_tool(SEND_EMAIL_SCHEMA, make_send_email_handler(email_client))
    dispatcher.register_tool(STORE_MEMORY_SCHEMA, make_store_memory_handler(memory_store))
    dispatcher.register_tool(FETCH_MEMORIES_SCHEMA, make_fetch_memories_handler(memory_store))
    summary_agent = SummaryAgent(dispatcher, response_composer)
    insight_agent = InsightAgent(dispatcher, response_composer)
    ocr_agent = OcrAgent(
        dispatcher,
        response_composer,
        settings,
        llm_adapter=llm_adapter,
        runtime_models=runtime_models,
    )
    forecast_agent = ForecastAgent(
        dispatcher,
        response_composer,
        response_cache,
        llm_model=settings.llm_model,
        narration_cache_ttl_seconds=settings.forecast_narration_cache_ttl_seconds,
    )
    ap_agent = ApAgent(dispatcher, response_composer)

    # Constructed only after dispatcher (needs store_memory/fetch_memories
    # registered), response_composer (needs synthesize_memory_fact), and
    # permission_provider (needs get_user_context for user_id scoping)
    # all exist — see app/agents/chat_agent.py.
    chat_agent = ChatAgent(llm_adapter, dispatcher, response_composer, permission_provider)

    pending_action_store = PendingActionStore(redis_client, settings.pending_action_ttl_seconds)
    mail_agent = MailAgent(pending_action_store, response_composer)

    rate_limiter = RateLimiter(
        redis_client,
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    audit_store = AuditStore(db_pool)

    agent_router = AgentRouter(chat_agent)
    agent_router.register(Intent.SEARCH, search_agent.handle)
    agent_router.register(Intent.SUMMARY, summary_agent.handle)
    agent_router.register(Intent.INSIGHT, insight_agent.handle)
    agent_router.register(Intent.OCR, ocr_agent.handle)
    agent_router.register(Intent.FORECAST, forecast_agent.handle)
    agent_router.register(Intent.AP, ap_agent.handle)
    agent_router.register(Intent.MAIL, mail_agent.handle)

    app.state.redis_client = redis_client
    app.state.db_pool = db_pool
    app.state.context_manager = context_manager
    app.state.intent_router = intent_router
    app.state.agent_router = agent_router
    app.state.response_composer = response_composer
    app.state.vector_store = vector_store
    app.state.embedding_adapter = embedding_adapter
    app.state.dispatcher = dispatcher
    app.state.pending_action_store = pending_action_store
    app.state.rate_limiter = rate_limiter
    app.state.permission_provider = permission_provider
    app.state.audit_store = audit_store
    app.state.memory_store = memory_store
    app.state.response_cache = response_cache
    # Exposed for the Test Console's GET/POST /console/llm-config below —
    # the same shared instance every agent already calls through, so
    # reconfiguring it here takes effect everywhere with no app restart.
    app.state.llm_adapter = llm_adapter
    app.state.runtime_models = runtime_models

    yield

    await redis_client.aclose()
    await db_pool.close()


app = FastAPI(title="AI Orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(AuditMiddleware)
# Backs GET /console's logo (app/static/ezofis-logo-mark.png) and any
# other static asset dropped in app/static/. Same-origin, so the console
# page's fetch() calls to /chat etc. need no CORS setup either.
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/console", response_class=HTMLResponse)
async def console() -> HTMLResponse:
    """A themed, self-contained manual test page for POST /chat and POST
    /actions/{action_id}/confirm — a dev convenience, not a phase
    deliverable, same "not a user-facing capability" exemption as
    /health and /metrics: no content filter / rate limit / permission
    check, it just serves a static file. All the actual API calls it
    makes from the browser go through the real, unmodified /chat and
    /actions/confirm routes below — this route only serves the page
    itself."""
    return HTMLResponse(content=_CONSOLE_HTML_PATH.read_text(encoding="utf-8"))


class LLMConfigUpdate(BaseModel):
    """Body for POST /console/llm-config. Every field is optional — only
    the fields you send are changed. Prefer `default_preset_id` /
    `fallback_preset_id` (Azure presets; keys stay in .env). `preset_id`
    is an alias for `default_preset_id`. Send an empty string for
    `fallback_preset_id` to clear it, or for `api_base`/`api_key`/
    `api_version` to clear those manual overrides."""

    default_preset_id: Optional[str] = None
    fallback_preset_id: Optional[str] = None
    preset_id: Optional[str] = None  # alias for default_preset_id
    model: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    api_version: Optional[str] = None


def _llm_config_response(request: Request) -> dict:
    llm_adapter: LLMAdapter = request.app.state.llm_adapter
    runtime: RuntimeModelSelection = request.app.state.runtime_models
    return {**llm_adapter.describe(), **runtime.describe()}


@app.get("/console/llm-presets")
async def get_llm_presets() -> dict:
    """Hardcoded Azure OpenAI deployments the Test Console can switch
    between. Never includes API keys — those stay server-side in
    .env and are applied when POST /console/llm-config sends a preset id."""
    return {"presets": list_presets_public(), "default_preset_id": DEFAULT_PRESET_ID}


@app.get("/console/llm-config")
async def get_llm_config(request: Request) -> dict:
    """Current LLM model/endpoint config, safe to return over the wire —
    never the API key's value, only whether one is set (see
    LLMAdapter.describe). Includes default/fallback preset ids for OCR."""
    return _llm_config_response(request)


@app.post("/console/llm-config")
async def update_llm_config(payload: LLMConfigUpdate, request: Request) -> dict:
    """Runtime model selection from the Test Console. Preset switches
    apply model/base/key from .env — no key needed in the UI.
    Selection is written to Redis so it survives hosting restarts; it only
    changes again when an operator Saves a new choice."""
    llm_adapter: LLMAdapter = request.app.state.llm_adapter
    runtime: RuntimeModelSelection = request.app.state.runtime_models
    redis_client = request.app.state.redis_client

    default_id = payload.default_preset_id if payload.default_preset_id is not None else payload.preset_id
    if default_id is not None and default_id != "":
        if get_preset(default_id) is None:
            raise HTTPException(status_code=400, detail=f"Unknown default_preset_id: {default_id}")
        apply_preset(llm_adapter, default_id)
        runtime.set_default(default_id)

    if payload.fallback_preset_id is not None:
        try:
            runtime.set_fallback(payload.fallback_preset_id or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Manual override path (Advanced) — only when no default preset was sent.
    if default_id is None or default_id == "":
        if any(
            v is not None
            for v in (payload.model, payload.api_base, payload.api_key, payload.api_version)
        ):
            llm_adapter.configure(
                model=payload.model,
                api_base=payload.api_base,
                api_key=payload.api_key,
                api_version=payload.api_version,
            )

    # Persist whenever a preset selection field was part of the request.
    if (default_id is not None and default_id != "") or payload.fallback_preset_id is not None:
        try:
            await runtime.save_to_redis(redis_client)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Model selection could not be saved; please try again.",
            ) from exc

    return _llm_config_response(request)


@app.post("/console/llm-test")
async def test_llm_config(request: Request) -> dict:
    """Fires one small, real request at whatever the LLM adapter is
    CURRENTLY configured for (after any /console/llm-config update) and
    reports pass/fail — a direct connectivity/auth check that doesn't
    touch session state, rate limits, or the guardrail pipeline at all,
    unlike a real /chat call. Never raises HTTPException on a provider
    failure: the point of this endpoint is to report that failure back
    to the console UI as data, not as a 502."""
    llm_adapter: LLMAdapter = request.app.state.llm_adapter
    try:
        result = await llm_adapter.chat_completion(
            [{"role": "user", "content": "Reply with exactly: api-ok"}]
        )
        return {"ok": True, "reply": result["content"], "usage": result["usage"]}
    except LLMAdapterError as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text-exposition format (Phase 5d) — pure aggregate
    counts/rates, never request/response content or any identifier (see
    app/control/metrics.py's docstring and
    tests/test_metrics_no_identifier_leakage.py). Deliberately exempt from
    the guardrails pipeline (content filter / rate limit / permission
    check) — an operational endpoint, not a user-facing capability, same
    as /health above; it simply never calls those functions."""
    return Response(content=render_latest(), media_type=CONTENT_TYPE_LATEST)


_CHAT_MULTIPART_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Required session id."},
        "message": {"type": "string", "description": "Chat text (optional for intent=ocr with file/filepath)."},
        "intent": {
            "type": "string",
            "description": "Explicit agent (ocr, ap, chat, …). Omit to use keyword routing.",
        },
        "instruction": {
            "type": "string",
            "description": "OCR hints (region / date format).",
        },
        "filepath": {
            "type": "string",
            "description": "Blob URL or container/blob path.",
        },
        "pageno": {
            "type": "string",
            "description": "Page: omit/1..5 = one page; -1 = up to 5 pages.",
        },
        "parameters": {
            "type": "array",
            "items": {"type": "string"},
            "description": 'One entry per field as Name,TYPE — e.g. Invoice No,SHORT_TEXT',
            "example": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
        },
        "tableparameters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional table field definitions (same Name,TYPE format).",
            "example": [],
        },
        "model": {"type": "string", "description": "Optional LLM model override."},
        "file": {
            "type": "string",
            "format": "binary",
            "description": "Upload any OCR-supported file (pdf/png/jpg/…). Wins over filepath.",
        },
    },
    "required": ["session_id"],
}


@app.post(
    "/chat",
    response_model=ChatResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                # JSON payload textbox in Swagger (select application/json).
                "application/json": {
                    "schema": ChatRequest.model_json_schema(
                        ref_template="#/components/schemas/{model}"
                    ),
                    "examples": {
                        "chat": {
                            "summary": "Plain chat",
                            "value": {"session_id": "demo", "message": "Hello"},
                        },
                        "ocr_blob": {
                            "summary": "OCR from blob path",
                            "value": {
                                "session_id": "demo",
                                "intent": "ocr",
                                "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
                                "payload": {
                                    "filepath": "ezts2e3b7b3738a34f94878ea006dad93230/INV26-27002140.pdf",
                                    "pageno": "1",
                                    "parameters": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
                                    "tableparameters": [],
                                },
                            },
                        },
                    },
                },
                # File browser in Swagger (select multipart/form-data).
                "multipart/form-data": {"schema": _CHAT_MULTIPART_SCHEMA},
            },
        }
    },
)
async def chat(request: Request, background_tasks: BackgroundTasks) -> ChatResponse:
    started_at = time.perf_counter()
    parsed = await parse_chat_request(request)
    payload = parsed.chat

    context_manager: ContextManager = request.app.state.context_manager
    intent_router: IntentRouter = request.app.state.intent_router
    agent_router: AgentRouter = request.app.state.agent_router
    response_composer: ResponseComposer = request.app.state.response_composer
    rate_limiter: RateLimiter = request.app.state.rate_limiter
    permission_provider: MockPermissionProvider = request.app.state.permission_provider
    audit_store: AuditStore = request.app.state.audit_store

    # Build a filterable / history message for document jobs.
    has_upload = parsed.file_bytes is not None  # empty bytes still count as an upload attempt
    has_filepath = bool(payload.payload and (payload.payload.filepath or "").strip())
    message = (payload.message or "").strip()
    explicit = (payload.intent or "").strip().lower()
    if not message:
        if has_filepath or has_upload or explicit == "ocr":
            message = (payload.instruction or "").strip() or "Process the document and generate structured JSON."
        else:
            raise HTTPException(status_code=422, detail="message is required.")

    request.state.session_id = payload.session_id
    request.state.raw_message = message
    request.state.intent = None

    # Gate 1: content filter — cheapest, stateless, no I/O.
    try:
        check_content(message)
    except ContentFilterRejectedError as exc:
        raise HTTPException(status_code=400, detail="Message rejected by content filter.") from exc

    # Gate 2: rate limit — one Redis round trip, keyed by session_id.
    try:
        await rate_limiter.check(payload.session_id)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded, please slow down.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimiterStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Rate limiter is currently unavailable, please try again."
        ) from exc

    try:
        history = await context_manager.get_history(payload.session_id)
    except SessionStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Session store is currently unavailable, please try again."
        ) from exc

    # Explicit intent overrides keyword router. Empty/omitted intent keeps
    # legacy keyword classification (chat/search/...) so existing clients
    # keep working. Document OCR requires intent=ocr (filepath alone never
    # forces OCR / AP).
    if explicit:
        try:
            intent = Intent(explicit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown intent '{payload.intent}'.") from exc
    else:
        intent = await intent_router.classify(message)
    request.state.intent = intent.value

    document_job = None
    if intent == Intent.OCR and (has_filepath or (parsed.file_bytes is not None)):
        if parsed.file_bytes is not None and len(parsed.file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        # Prefer upload over filepath when both are present.
        try:
            resolve_pageno(
                payload.payload.pageno if payload.payload else None,
                max_pages=get_settings().ocr_max_pages,
            )
        except InvalidOcrPageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        document_job = {
            "instruction": payload.instruction,
            "filepath": None if parsed.file_bytes is not None else (payload.payload.filepath if payload.payload else None),
            "file_bytes": parsed.file_bytes,
            "filename": parsed.filename if parsed.file_bytes is not None else None,
            "content_type": parsed.content_type if parsed.file_bytes is not None else None,
            "pageno": payload.payload.pageno if payload.payload else None,
            "parameters": list(payload.payload.parameters) if payload.payload else [],
            "tableparameters": list(payload.payload.tableparameters) if payload.payload else [],
            "model": payload.payload.model if payload.payload else None,
        }
    elif intent == Intent.OCR and explicit == "ocr" and not (has_filepath or parsed.file_bytes is not None):
        # Explicit OCR without a document still allows legacy "run ocr on SCN-.." messages.
        pass

    # Gate 3: permission check — needs the classified intent, so it can
    # only run here, not earlier. The Dispatcher/agent must never be
    # invoked past this point for a denied intent.
    user_context = await permission_provider.get_user_context(payload.session_id)
    try:
        check_permission(user_context, intent)
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail="Not permitted to use this capability.") from exc

    try:
        result = await agent_router.route(
            intent,
            session_id=payload.session_id,
            message=message,
            history=history,
            document_job=document_job,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAdapterError as exc:
        raise HTTPException(
            status_code=502, detail="Upstream LLM provider error, please try again."
        ) from exc
    except EmbeddingAdapterError as exc:
        raise HTTPException(
            status_code=502, detail="Upstream embedding provider error, please try again."
        ) from exc
    except VectorStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Document store is currently unavailable, please try again."
        ) from exc
    except ToolExecutionError as exc:
        raise HTTPException(
            status_code=502, detail="Upstream service error, please try again."
        ) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    try:
        await context_manager.append_turn(payload.session_id, "user", message)
        await context_manager.append_turn(payload.session_id, "assistant", result["reply"])
    except SessionStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Session store is currently unavailable, please try again."
        ) from exc

    request.state.token_usage = result.get("usage")

    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

    background_tasks.add_task(
        audit_store.record,
        correlation_id=request.state.correlation_id,
        session_id=payload.session_id,
        intent=intent.value,
        event_type="request_completed",
        status="success",
        latency_ms=latency_ms,
        redacted_request_snippet=_snippet_for_audit(intent.value, message),
        redacted_response_snippet=_snippet_for_audit(intent.value, result["reply"]),
    )

    return response_composer.compose_chat_response(
        session_id=payload.session_id,
        reply=result["reply"],
        correlation_id=request.state.correlation_id,
        latency_ms=latency_ms,
        token_usage=result.get("usage"),
        chunk_ids=result.get("chunk_ids"),
        document_id=result.get("document_id"),
        cited_data_points=result.get("cited_data_points"),
        ocr_result=result.get("ocr_result"),
        forecast_result=result.get("forecast_result"),
        invoice_reference=result.get("invoice_reference"),
        mail_draft=result.get("mail_draft"),
    )


@app.post("/actions/{action_id}/confirm", response_model=ConfirmActionResponse)
async def confirm_action(
    action_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="Session id, for rate limiting and permission checks."),
) -> ConfirmActionResponse:
    """Executes a pending confirmation-gated action for real — currently
    only Mail's send_email creates pending actions, but this endpoint is
    generic over tool_name, same as the Dispatcher gate it drives.

    The same three guardrails as /chat apply here first, in the same
    fail-fast order, and all *before* the pending action is looked up:
      1. Content filter, run against `action_id` itself (this endpoint has
         no free-text message to check).
      2. Rate limit, keyed by the `session_id` query param — confirm has
         no session concept of its own otherwise, so this is required.
      3. A confirm-specific, intent-agnostic permission check
         (check_confirm_permission) — the tool_name isn't known until
         after the pending action is looked up, which only happens once
         these gates pass, so this can't check a specific intent's
         permission the way /chat does.

    Not found / expired / already confirmed -> 404 (all indistinguishable
    at the store layer, and all mean the same thing to the caller: there's
    nothing left to confirm). Redis outage -> 503, same discipline as
    every other Redis-backed store in this app. A failure in the
    underlying tool itself -> 502, same discipline as every other tool
    failure (see ToolExecutionError in app/core/dispatcher.py).
    """
    pending_action_store: PendingActionStore = request.app.state.pending_action_store
    dispatcher: Dispatcher = request.app.state.dispatcher
    rate_limiter: RateLimiter = request.app.state.rate_limiter
    permission_provider: MockPermissionProvider = request.app.state.permission_provider
    audit_store: AuditStore = request.app.state.audit_store

    request.state.session_id = session_id
    # confirm never has a free-text message to snippet, and today's only
    # gated tool is Mail — so request.state.raw_message is deliberately
    # never set here, guaranteeing _snippet_for_audit always returns None
    # for every confirm audit row, regardless of intent.
    request.state.intent = None

    # Gate 1: content filter, against action_id (no message on confirm).
    try:
        check_content(action_id)
    except ContentFilterRejectedError as exc:
        raise HTTPException(status_code=400, detail="Request rejected by content filter.") from exc

    # Gate 2: rate limit, keyed by the session_id query param.
    try:
        await rate_limiter.check(session_id)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded, please slow down.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimiterStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Rate limiter is currently unavailable, please try again."
        ) from exc

    # Gate 3: confirm-specific permission check (intent-agnostic — see
    # docstring above).
    user_context = await permission_provider.get_user_context(session_id)
    try:
        check_confirm_permission(user_context)
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail="Not permitted to confirm actions.") from exc

    # Look up (not consume) first, so a wrong-session guess can't burn the
    # legitimate owner's pending action before they get to confirm it —
    # only a session match below actually consumes it.
    try:
        pending_action = await pending_action_store.get(action_id)
    except PendingActionStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Pending action store is currently unavailable, please try again."
        ) from exc

    if pending_action is None or pending_action.session_id != session_id:
        # Deliberately the SAME response for "doesn't exist" and "exists
        # but belongs to a different session" — a distinct error here
        # would leak which action_ids are valid to a caller who doesn't
        # own them.
        logger.info("action_confirm_not_found", extra={"action_id": action_id, "outcome": "not_found"})
        raise HTTPException(
            status_code=404,
            detail="No pending action found for that id — it may have expired or already been confirmed.",
        )

    try:
        pending_action = await pending_action_store.consume(action_id)
    except PendingActionStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Pending action store is currently unavailable, please try again."
        ) from exc

    if pending_action is None:
        # Narrow race: expired or consumed by a concurrent request between
        # the get() above and this consume(). Same not-found response.
        logger.info("action_confirm_not_found", extra={"action_id": action_id, "outcome": "not_found"})
        raise HTTPException(
            status_code=404,
            detail="No pending action found for that id — it may have expired or already been confirmed.",
        )

    # The tool is known now, so the audit handler (on the failure branch
    # below) and the success write further down can both report a real
    # intent — still never a snippet, since raw_message was never set.
    request.state.intent = _TOOL_NAME_TO_INTENT.get(pending_action.tool_name)

    try:
        result = await dispatcher.dispatch_confirmed(pending_action.tool_name, pending_action.arguments)
    except ToolExecutionError as exc:
        logger.warning(
            "action_confirm_tool_failed",
            extra={"action_id": action_id, "tool_name": pending_action.tool_name, "outcome": "error"},
        )
        raise HTTPException(
            status_code=502, detail="Upstream service error while executing the confirmed action, please try again."
        ) from exc

    # Recipient/subject were already logged when the draft was created;
    # the result dict itself (see EmailClient.send_email) never includes
    # the body, so logging it here can't leak it either.
    logger.info(
        "action_confirmed",
        extra={"action_id": action_id, "tool_name": pending_action.tool_name, "outcome": "executed"},
    )

    background_tasks.add_task(
        audit_store.record,
        correlation_id=request.state.correlation_id,
        session_id=session_id,
        intent=request.state.intent,
        event_type="action_confirmed",
        status="success",
        latency_ms=None,
        redacted_request_snippet=None,
        redacted_response_snippet=None,
    )

    return ConfirmActionResponse(
        action_id=action_id,
        tool_name=pending_action.tool_name,
        status="executed",
        result=result,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler_with_audit(request: Request, exc: HTTPException) -> JSONResponse:
    """Overrides FastAPI's default HTTPException handler for exactly one
    reason: to attach a best-effort audit_log write as a background task.

    The response this builds is otherwise byte-identical to FastAPI's
    default (`{"detail": exc.detail}`, same status_code, same headers —
    see `fastapi.exception_handlers.http_exception_handler`), so no
    existing test's assertions about response shape change.

    Why here and not the `BackgroundTasks` dependency used on the success
    paths: a `BackgroundTasks` object populated inside a route function
    that then raises is never seen by FastAPI's exception-to-response
    conversion — it's a different code path with no access to those
    locals. This handler is the one place guaranteed to see every
    HTTPException this app raises (every guardrail rejection, every
    upstream/store failure, on both endpoints), so it reads whatever
    `request.state.*` fields the route already set before raising
    (`session_id` always; `intent`/`raw_message` only where relevant —
    both default to None via `getattr`, which is exactly right for
    pre-classification rejections and for confirm, which never sets
    `raw_message` at all).
    """
    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    audit_store: AuditStore = request.app.state.audit_store
    intent = getattr(request.state, "intent", None)
    event_type = _EVENT_TYPE_BY_STATUS_CODE.get(exc.status_code, "request_failed")
    # Phase 5d: only content_filtered/rate_limited/permission_denied are
    # genuine guardrail rejections — record_guardrail_rejection silently
    # ignores every other event_type (action_not_found, upstream_error,
    # ...) that also flows through this same handler, see
    # app/control/metrics.py.
    record_guardrail_rejection(reason=event_type)
    response.background = BackgroundTask(
        audit_store.record,
        correlation_id=getattr(request.state, "correlation_id", "-"),
        session_id=getattr(request.state, "session_id", None),
        intent=intent,
        event_type=event_type,
        status=_status_bucket(exc.status_code),
        latency_ms=None,
        redacted_request_snippet=_snippet_for_audit(intent, getattr(request.state, "raw_message", None)),
        redacted_response_snippet=None,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Belt-and-braces safety net: any exception not already turned into an
    HTTPException above is logged server-side (with correlation ID, never a
    stack trace in the response) and reported to the caller as a generic
    500 — no internals leak out."""
    correlation_id = getattr(request.state, "correlation_id", "-")
    logger.error(
        "unhandled_exception",
        extra={"correlation_id": correlation_id, "error_type": type(exc).__name__},
    )
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "correlation_id": correlation_id},
    )

    audit_store: AuditStore = request.app.state.audit_store
    response.background = BackgroundTask(
        audit_store.record,
        correlation_id=correlation_id,
        session_id=getattr(request.state, "session_id", None),
        intent=getattr(request.state, "intent", None),
        event_type="unhandled_exception",
        status="error",
        latency_ms=None,
        redacted_request_snippet=None,
        redacted_response_snippet=None,
    )
    return response
