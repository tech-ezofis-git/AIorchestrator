"""The eval harness's core: case models, case loading, the runner that
executes cases through the REAL, configured pipeline, and report
generation. See app/evals/run.py for the CLI entry point and README.md's
"Eval harness (Phase 5c)" section for how/when to actually run this.

This is deliberately NOT `pytest`. `pytest` proves deterministic code paths
against mocked LLM responses — the same input always produces the same
mocked output, forever, which is exactly what makes it safe to run on
every commit with no API key. This harness proves something pytest
structurally cannot: whether the REAL, configured LLM_MODEL (and
EMBEDDING_MODEL, for Search) actually produces GOOD output for a curated
set of representative cases — citations that make sense, summaries that
don't hallucinate, forecasts whose narration matches the numbers, Chat
that actually honors a remembered preference, Mail drafts that are
on-topic. That requires a real API key and costs real money, which is
exactly why it's a separate, manually-invoked tool (rule 2) — never run by
`pytest` or `docker compose up`.

Non-goal: this harness does NOT exercise intent classification
(app/core/intent_router.py) or the guardrail pipeline (app/main.py) — each
case declares its target `intent` directly and the runner invokes that
agent's `.handle(...)` in-process, matching rule 9 ("exercise the app's
components directly, not over HTTP"). Routing correctness is pytest's job
(tests/test_*_endpoint.py); this harness's job is output QUALITY once
correctly routed.

Infra: every agent is wired up exactly as app/main.py wires it — same
classes, same call graph — except Postgres/Redis-backed STATE (vector
storage, Chat memory, response caching, pending actions) is backed by
infra-free stand-ins (app/evals/fixtures.py, and a real fakeredis client
for the two Redis-backed pieces) rather than a live database. This is
never faking what's under evaluation: LLMAdapter, EmbeddingAdapter, and
every already-mocked-by-design integration client (EzofisClient,
ForecastModelClient, EmailClient) are completely real and unmodified. A
FRESH pipeline (fresh vector store, fresh memory store, fresh response
cache, fresh pending-action store) is built per case, so no case's real
LLM/embedding call can ever be skipped by another case's leftover cached
state — every case gets a genuinely fresh computation to score.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Union

import fakeredis.aioredis
import yaml
from pydantic import BaseModel, Field

from app.agents.chat_agent import ChatAgent
from app.agents.forecast_agent import ForecastAgent
from app.agents.mail_agent import MailAgent
from app.agents.search_agent import SearchAgent
from app.agents.summary_agent import SummaryAgent
from app.config import Settings, get_settings
from app.control.audit import correlation_id_ctx
from app.control.permissions import MockPermissionProvider
from app.control.response_cache import ResponseCache
from app.core.dispatcher import Dispatcher
from app.core.pending_actions import PendingActionStore
from app.core.response_composer import ResponseComposer
from app.evals.fixtures import InMemoryMemoryStore, InMemoryVectorStore
from app.evals.scoring import ScoreResult, score_llm_judge, score_rule
from app.integrations.email_client import EmailClient
from app.integrations.ezofis_client import EzofisClient
from app.integrations.forecast_model import ForecastModelClient
from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.ingestion import IngestionPipeline
from app.llm.adapter import LLMAdapter
from app.llm.embedding_adapter import EmbeddingAdapter
from app.tools.fetch_document import FETCH_DOCUMENT_SCHEMA, make_fetch_document_handler
from app.tools.fetch_invoice_status import FETCH_INVOICE_STATUS_SCHEMA, make_fetch_invoice_status_handler
from app.tools.fetch_memories import FETCH_MEMORIES_SCHEMA, make_fetch_memories_handler
from app.tools.fetch_report_data import FETCH_REPORT_DATA_SCHEMA, make_fetch_report_data_handler
from app.tools.run_forecast import RUN_FORECAST_SCHEMA, make_run_forecast_handler
from app.tools.send_email import SEND_EMAIL_SCHEMA, make_send_email_handler
from app.tools.store_memory import STORE_MEMORY_SCHEMA, make_store_memory_handler

logger = logging.getLogger("orchestrator.evals.runner")

CASES_DIR = Path(__file__).parent / "cases"

# Every intent an eval case may target -> the pipeline dict key the runner
# looks the corresponding agent up by. Kept explicit (not "whatever's in
# the YAML") so a typo'd `intent:` in a case file fails clearly.
_SUPPORTED_INTENTS = ("chat", "search", "summary", "forecast", "mail")


# --- Case schema (YAML -> pydantic) -----------------------------------


class SetupDocument(BaseModel):
    """One document to ingest into a fresh, per-case in-memory vector
    store before the case's turns run. Inline `text`, not a file
    reference — keeps a case's exact input fully visible in its own YAML
    file, and the harness self-contained (no dependency on tests/
    fixtures)."""

    title: Optional[str] = None
    text: str


class CaseSetup(BaseModel):
    documents: list[SetupDocument] = Field(default_factory=list)


class CaseTurn(BaseModel):
    """One message in a case. `session_id` is optional — auto-generated
    per case+turn if omitted; only Chat memory-honoring cases need to set
    it explicitly (a distinct session_id per turn, to prove the memory
    persists ACROSS sessions, not just within one)."""

    session_id: Optional[str] = None
    message: str


class CaseInput(BaseModel):
    turns: list[CaseTurn]


class RuleScoring(BaseModel):
    """Objective, deterministic scoring — see app/evals/scoring.py's
    RULE_FUNCTIONS for the registered `function` names."""

    method: Literal["rule"]
    function: str
    args: dict[str, Any] = Field(default_factory=dict)


class LLMJudgeScoring(BaseModel):
    """Subjective quality scoring via one call to JUDGE_MODEL (rule 4).
    `pass_threshold` is compared against the judge's 1-5 score."""

    method: Literal["llm_judge"]
    rubric: str
    pass_threshold: float = 4.0


class EvalCase(BaseModel):
    id: str
    description: Optional[str] = None
    intent: str
    setup: Optional[CaseSetup] = None
    input: CaseInput
    scoring: Union[RuleScoring, LLMJudgeScoring] = Field(discriminator="method")


def load_cases(cases_dir: Optional[Path] = None) -> list[EvalCase]:
    """Loads every case from every `*.yaml` file in `cases_dir` (default:
    app/evals/cases/), sorted by filename then file order, for a
    deterministic run order. Each file has a top-level `cases:` list."""
    directory = cases_dir or CASES_DIR
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw_case in data.get("cases", []):
            cases.append(EvalCase.model_validate(raw_case))
    return cases


# --- Results / report ---------------------------------------------------


@dataclass
class EvalCaseResult:
    case_id: str
    intent: str
    description: Optional[str]
    passed: bool
    score: Optional[float]
    detail: str
    output: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class EvalReport:
    run_id: str
    generated_at: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    average_score: Optional[float]
    results: list[EvalCaseResult]


def build_report(run_id: str, results: list[EvalCaseResult]) -> EvalReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    scores = [r.score for r in results if r.score is not None]
    average_score = (sum(scores) / len(scores)) if scores else None
    return EvalReport(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=(passed / total) if total else 0.0,
        average_score=average_score,
        results=results,
    )


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# Eval report",
        "",
        f"- Run id: `{report.run_id}`",
        f"- Generated at: {report.generated_at}",
        f"- Cases: {report.total}",
        f"- Passed: {report.passed}",
        f"- Failed: {report.failed}",
        f"- Pass rate: {report.pass_rate:.0%}",
        f"- Average score (cases with a numeric score): "
        f"{report.average_score:.2f}" if report.average_score is not None else "- Average score: n/a",
        "",
        "## Per-case results",
        "",
        "| Case | Intent | Result | Score | Detail |",
        "|---|---|---|---|---|",
    ]
    for r in report.results:
        result_text = "PASS" if r.passed else "FAIL"
        score_text = f"{r.score:.2f}" if r.score is not None else "n/a"
        detail = (r.error or r.detail).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.case_id} | {r.intent} | {result_text} | {score_text} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def render_json(report: EvalReport) -> str:
    payload = {
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "pass_rate": report.pass_rate,
        "average_score": report.average_score,
        "results": [
            {
                "case_id": r.case_id,
                "intent": r.intent,
                "description": r.description,
                "passed": r.passed,
                "score": r.score,
                "detail": r.detail,
                "output": r.output,
                "error": r.error,
            }
            for r in report.results
        ],
    }
    return json.dumps(payload, indent=2, default=str)


# --- Runner ---------------------------------------------------------------


class EvalRunner:
    """Builds the real, stateless pipeline components once (LLM adapter,
    embedding adapter, judge LLM adapter, response composer, the mocked-
    by-design integration clients, the permission provider), and a fresh
    STATEFUL pipeline (vector store, memory store, response cache, pending
    action store, and the agents wired against them) per case — see the
    module docstring for why."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()

        self._llm_adapter = LLMAdapter(self._settings)
        self._embedding_adapter = EmbeddingAdapter(self._settings)

        # Rule 4: JUDGE_MODEL, defaulting to LLM_MODEL if unset — a
        # separate LLMAdapter instance, never LLM_MODEL directly, so a
        # different/stronger model can judge than the one under test.
        judge_model = self._settings.judge_model or self._settings.llm_model
        judge_settings = self._settings.model_copy(update={"llm_model": judge_model})
        self._judge_llm_adapter = LLMAdapter(judge_settings)

        self._response_composer = ResponseComposer(self._llm_adapter)
        self._ezofis_client = EzofisClient()
        self._forecast_model_client = ForecastModelClient()
        self._email_client = EmailClient()
        self._permission_provider = MockPermissionProvider()

    def _build_case_pipeline(self) -> tuple[dict[str, Any], InMemoryVectorStore]:
        """Fresh, per-case: infra-free stand-ins for every piece of state,
        and every agent wired against them exactly as app/main.py wires
        the real ones. Returns (agents_by_intent, vector_store) — the
        vector store is returned separately so the caller can ingest a
        case's setup documents into it before invoking the agent."""
        vector_store = InMemoryVectorStore()
        memory_store = InMemoryMemoryStore()
        response_cache = ResponseCache(fakeredis.aioredis.FakeRedis())
        pending_action_store = PendingActionStore(
            fakeredis.aioredis.FakeRedis(), self._settings.pending_action_ttl_seconds
        )

        hybrid_search = HybridSearch(vector_store, self._embedding_adapter)
        search_agent = SearchAgent(
            hybrid_search,
            self._response_composer,
            self._embedding_adapter,
            response_cache,
            top_n=self._settings.search_top_n,
            embedding_model=self._settings.embedding_model,
            llm_model=self._settings.llm_model,
            embedding_cache_ttl_seconds=self._settings.embedding_cache_ttl_seconds,
            result_cache_ttl_seconds=self._settings.search_result_cache_ttl_seconds,
        )

        dispatcher = Dispatcher()
        dispatcher.register_tool(FETCH_DOCUMENT_SCHEMA, make_fetch_document_handler(self._ezofis_client))
        dispatcher.register_tool(FETCH_REPORT_DATA_SCHEMA, make_fetch_report_data_handler(self._ezofis_client))
        dispatcher.register_tool(RUN_FORECAST_SCHEMA, make_run_forecast_handler(self._forecast_model_client))
        dispatcher.register_tool(
            FETCH_INVOICE_STATUS_SCHEMA, make_fetch_invoice_status_handler(self._ezofis_client)
        )
        dispatcher.register_tool(SEND_EMAIL_SCHEMA, make_send_email_handler(self._email_client))
        dispatcher.register_tool(STORE_MEMORY_SCHEMA, make_store_memory_handler(memory_store))
        dispatcher.register_tool(FETCH_MEMORIES_SCHEMA, make_fetch_memories_handler(memory_store))

        summary_agent = SummaryAgent(dispatcher, self._response_composer)
        forecast_agent = ForecastAgent(
            dispatcher,
            self._response_composer,
            response_cache,
            llm_model=self._settings.llm_model,
            narration_cache_ttl_seconds=self._settings.forecast_narration_cache_ttl_seconds,
        )
        mail_agent = MailAgent(pending_action_store, self._response_composer)
        chat_agent = ChatAgent(self._llm_adapter, dispatcher, self._response_composer, self._permission_provider)

        agents: dict[str, Any] = {
            "chat": chat_agent,
            "search": search_agent,
            "summary": summary_agent,
            "forecast": forecast_agent,
            "mail": mail_agent,
        }
        return agents, vector_store

    async def _ingest_setup_documents(
        self, vector_store: InMemoryVectorStore, documents: list[SetupDocument]
    ) -> None:
        pipeline = IngestionPipeline(
            vector_store,
            self._embedding_adapter,
            chunk_size_tokens=self._settings.chunk_size_tokens,
            overlap_tokens=self._settings.chunk_overlap_tokens,
        )
        for doc in documents:
            await pipeline.ingest_text(source="eval-case", title=doc.title, text=doc.text)

    async def _score(self, case: EvalCase, output: dict[str, Any]) -> ScoreResult:
        if isinstance(case.scoring, RuleScoring):
            return score_rule(output, function=case.scoring.function, args=case.scoring.args)
        return await score_llm_judge(
            judge_llm_adapter=self._judge_llm_adapter,
            rubric=case.scoring.rubric,
            output_text=json.dumps(output, indent=2, default=str),
            pass_threshold=case.scoring.pass_threshold,
        )

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        """Runs one case end to end: build a fresh pipeline, ingest any
        setup documents, run every turn in order (the LAST turn's output
        is what gets scored — the only one that matters for multi-turn
        cases like Chat memory-honoring, where earlier turns exist purely
        to set state), then score. Never raises — a case that fails to
        even run (an unregistered intent, a pipeline exception) is
        recorded as a failed result, same as a case that runs but scores
        badly, so one bad case can't abort an entire run.
        """
        if case.intent not in _SUPPORTED_INTENTS:
            detail = f"unsupported intent '{case.intent}' — must be one of {_SUPPORTED_INTENTS}"
            logger.warning("eval_case_invalid", extra={"case_id": case.id, "detail": detail})
            return EvalCaseResult(
                case_id=case.id, intent=case.intent, description=case.description,
                passed=False, score=None, detail=detail, output={}, error=detail,
            )

        try:
            agents, vector_store = self._build_case_pipeline()
            if case.setup and case.setup.documents:
                await self._ingest_setup_documents(vector_store, case.setup.documents)

            agent = agents[case.intent]
            output: dict[str, Any] = {}
            for turn_index, turn in enumerate(case.input.turns):
                session_id = turn.session_id or f"eval-{case.id}-{turn_index}"
                output = await agent.handle(session_id=session_id, message=turn.message, history=[])

            score_result = await self._score(case, output)
            logger.info(
                "eval_case_completed",
                extra={"case_id": case.id, "intent": case.intent, "passed": score_result.passed, "score": score_result.score},
            )
            return EvalCaseResult(
                case_id=case.id, intent=case.intent, description=case.description,
                passed=score_result.passed, score=score_result.score, detail=score_result.detail, output=output,
            )
        except Exception as exc:
            logger.warning("eval_case_failed", extra={"case_id": case.id, "error_type": type(exc).__name__})
            return EvalCaseResult(
                case_id=case.id, intent=case.intent, description=case.description,
                passed=False, score=None, detail=f"case raised {type(exc).__name__}: {exc}", output={}, error=str(exc),
            )

    async def run_all(self, cases: list[EvalCase]) -> EvalReport:
        """Runs every case in order and aggregates into one report. Tags
        every log line emitted during the run with a single correlation_id
        (rule 12), same mechanism app/control/audit.py's AuditMiddleware
        uses for a real request — just set once, for the whole run,
        instead of once per HTTP request."""
        run_id = str(uuid.uuid4())
        token = correlation_id_ctx.set(run_id)
        try:
            logger.info("eval_run_started", extra={"case_count": len(cases)})
            results = [await self.run_case(case) for case in cases]
            report = build_report(run_id, results)
            logger.info(
                "eval_run_completed",
                extra={"total": report.total, "passed": report.passed, "failed": report.failed},
            )
            return report
        finally:
            correlation_id_ctx.reset(token)
