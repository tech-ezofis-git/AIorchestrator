"""Proves the eval harness's MECHANICS work correctly — case loading,
runner logic, scoring logic, report generation — entirely with mocked LLM/
embedding responses. This is NOT running real evals (that's
`python -m app.evals.run`, which needs a real API key and costs real
money — see README.md's "Eval harness (Phase 5c)" section). No test in
this file makes a real network call: `LLMAdapter.chat_completion` /
`EmbeddingAdapter.embed` are monkeypatched everywhere a case would
otherwise reach them, and an autouse fixture below fails loudly if
anything ever reaches the real litellm layer underneath, as a second line
of defense.
"""
import json
import textwrap

import pytest

from app.config import Settings
from app.evals.runner import (
    CaseInput,
    CaseSetup,
    CaseTurn,
    EvalCase,
    EvalCaseResult,
    EvalRunner,
    LLMJudgeScoring,
    RuleScoring,
    SetupDocument,
    build_report,
    load_cases,
    render_json,
    render_markdown,
)
from app.evals.scoring import (
    contains_all,
    contains_any,
    field_non_empty,
    matches_regex,
    numbers_from_field_present_in_text,
    score_llm_judge,
    score_rule,
)


@pytest.fixture(autouse=True)
def _forbid_real_network_calls(monkeypatch):
    """Belt-and-braces: every test below monkeypatches the adapters it
    needs directly, so nothing here should ever reach litellm at all — if
    it somehow did, fail immediately instead of hanging or spending real
    money."""

    async def _boom(*args, **kwargs):
        raise AssertionError("a real litellm call was attempted during a harness-mechanics test")

    monkeypatch.setattr("litellm.acompletion", _boom)
    monkeypatch.setattr("litellm.aembedding", _boom)


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


# --- Rule-based scoring ----------------------------------------------------


def test_field_non_empty():
    assert field_non_empty({"reply": "hi"}, field="reply").passed
    assert not field_non_empty({"reply": ""}, field="reply").passed
    assert not field_non_empty({}, field="missing").passed


def test_contains_all():
    output = {"reply": "PTO accrues at 1.5 days per month. [1]"}
    assert contains_all(output, field="reply", expected=["1.5 days", "[1]"]).passed
    assert not contains_all(output, field="reply", expected=["1.5 days", "nonexistent"]).passed


def test_contains_any():
    output = {"reply": "hello world"}
    assert contains_any(output, field="reply", expected=["world", "nope"]).passed
    assert not contains_any(output, field="reply", expected=["nope", "nada"]).passed


def test_matches_regex():
    output = {"mail_draft": {"recipient": "jane@example.com"}}
    assert matches_regex(output, field="mail_draft.recipient", pattern=r"^[^@]+@[^@]+\.[a-z]+$").passed
    assert not matches_regex(output, field="mail_draft.recipient", pattern=r"^not-an-email$").passed


def test_matches_regex_handles_missing_field():
    assert not matches_regex({}, field="mail_draft.recipient", pattern=r".").passed


def test_numbers_from_field_present_in_text_passes_when_all_numbers_mentioned():
    output = {
        "reply": "Revenue grows from 1000.0 to 1092.7 over the period.",
        "forecast_result": {"predicted_values": [1000.0, 1092.7]},
    }
    result = numbers_from_field_present_in_text(
        output, text_field="reply", numbers_field="forecast_result.predicted_values", tolerance=0.5
    )
    assert result.passed


def test_numbers_from_field_present_in_text_fails_when_a_number_is_missing():
    output = {
        "reply": "Revenue grows to 1092.7.",
        "forecast_result": {"predicted_values": [1000.0, 1092.7]},
    }
    result = numbers_from_field_present_in_text(
        output, text_field="reply", numbers_field="forecast_result.predicted_values", tolerance=0.5
    )
    assert not result.passed
    assert "1000" in result.detail


def test_score_rule_unknown_function_fails_cleanly():
    result = score_rule({}, function="does_not_exist", args={})
    assert not result.passed
    assert result.score is None


def test_score_rule_function_raising_is_caught_not_raised():
    # matches_regex requires 'pattern' — omitting it raises TypeError inside
    # the function, which score_rule must catch, not propagate.
    result = score_rule({"reply": "hi"}, function="matches_regex", args={"field": "reply"})
    assert not result.passed
    assert result.score is None
    assert "matches_regex" in result.detail


# --- LLM-judge scoring -------------------------------------------------


class _FakeJudgeAdapter:
    def __init__(self, content: str):
        self._content = content
        self.calls: list[list[dict]] = []

    async def chat_completion(self, messages):
        self.calls.append(messages)
        return {"content": self._content, "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


async def test_score_llm_judge_pass():
    adapter = _FakeJudgeAdapter("Score: 5\nJustification: Great answer.")

    result = await score_llm_judge(judge_llm_adapter=adapter, rubric="rate it", output_text="{}", pass_threshold=4)

    assert result.passed
    assert result.score == 5.0
    assert "Great answer" in result.detail
    assert len(adapter.calls) == 1


async def test_score_llm_judge_below_threshold_fails():
    adapter = _FakeJudgeAdapter("Score: 2\nJustification: Not great.")

    result = await score_llm_judge(judge_llm_adapter=adapter, rubric="rate it", output_text="{}", pass_threshold=4)

    assert not result.passed
    assert result.score == 2.0


async def test_score_llm_judge_unparseable_response_fails_cleanly():
    adapter = _FakeJudgeAdapter("I refuse to follow the requested format.")

    result = await score_llm_judge(judge_llm_adapter=adapter, rubric="rate it", output_text="{}", pass_threshold=4)

    assert not result.passed
    assert result.score is None


async def test_score_llm_judge_call_failure_fails_cleanly_not_raises():
    class _BrokenAdapter:
        async def chat_completion(self, messages):
            raise RuntimeError("simulated provider outage")

    result = await score_llm_judge(
        judge_llm_adapter=_BrokenAdapter(), rubric="rate it", output_text="{}", pass_threshold=4
    )

    assert not result.passed
    assert result.score is None


# --- Case loading --------------------------------------------------------


def test_load_cases_from_yaml(tmp_path):
    case_yaml = textwrap.dedent(
        """
        cases:
          - id: sample-rule-case
            intent: chat
            input:
              turns:
                - message: "hello"
            scoring:
              method: rule
              function: field_non_empty
              args:
                field: reply
        """
    )
    (tmp_path / "sample.yaml").write_text(case_yaml)

    cases = load_cases(tmp_path)

    assert len(cases) == 1
    assert cases[0].id == "sample-rule-case"
    assert isinstance(cases[0].scoring, RuleScoring)


def test_load_cases_across_multiple_files_sorted_by_filename(tmp_path):
    single_case = (
        "cases:\n  - id: {id}\n    intent: chat\n    input:\n      turns:\n        - message: hi\n"
        "    scoring:\n      method: rule\n      function: field_non_empty\n      args:\n        field: reply\n"
    )
    (tmp_path / "b.yaml").write_text(single_case.format(id="case-b"))
    (tmp_path / "a.yaml").write_text(single_case.format(id="case-a"))

    cases = load_cases(tmp_path)

    assert [c.id for c in cases] == ["case-a", "case-b"]


def test_bundled_case_files_all_load_and_validate():
    """The actual shipped case files (app/evals/cases/*.yaml) must be
    well-formed — this only proves the YAML/schema is valid, it does NOT
    run them (no API key needed for this)."""
    cases = load_cases()

    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique across all files"
    assert len(cases) >= 5

    intents = {c.intent for c in cases}
    assert intents == {"chat", "search", "summary", "forecast", "mail"}


# --- EvalRunner ------------------------------------------------------------


async def test_run_case_rule_scored_chat_case(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {"content": "Sure, here is a reply.", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    runner = EvalRunner(_settings())
    case = EvalCase(
        id="rule-chat-case",
        intent="chat",
        input=CaseInput(turns=[CaseTurn(message="hello")]),
        scoring=RuleScoring(method="rule", function="field_non_empty", args={"field": "reply"}),
    )

    result = await runner.run_case(case)

    assert result.passed
    assert result.error is None
    assert result.output["reply"] == "Sure, here is a reply."


async def test_run_case_search_case_ingests_setup_documents_and_scores(monkeypatch):
    _VOCAB = ["pto", "policy", "expense"]

    async def fake_embed(self, texts):
        return [[float(t.lower().count(w)) for w in _VOCAB] for t in texts]

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", fake_embed)

    async def fake_chat_completion(self, messages):
        return {"content": "PTO accrues at 1.5 days per month. [1]", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    runner = EvalRunner(_settings())
    case = EvalCase(
        id="search-case",
        intent="search",
        setup=CaseSetup(documents=[SetupDocument(title="PTO Policy", text="pto policy content about accrual pto policy")]),
        input=CaseInput(turns=[CaseTurn(message="How much PTO do I accrue?")]),
        scoring=RuleScoring(method="rule", function="matches_regex", args={"field": "reply", "pattern": r"\[\d+\]"}),
    )

    result = await runner.run_case(case)

    assert result.passed
    assert result.output["chunk_ids"]


async def test_run_case_llm_judge_scored_uses_a_separate_judge_model(monkeypatch):
    """Proves rule 4: judge scoring calls JUDGE_MODEL, not LLM_MODEL — two
    distinct LLMAdapter instances, distinguished here by their `_model`."""
    calls = []

    async def tracking_chat_completion(self, messages):
        calls.append(self._model)
        if self._model == "judge-model-x":
            return {"content": "Score: 5\nJustification: good.", "usage": None}
        return {"content": "a normal reply", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    runner = EvalRunner(_settings(llm_model="under-test-model", judge_model="judge-model-x"))
    case = EvalCase(
        id="judge-case",
        intent="chat",
        input=CaseInput(turns=[CaseTurn(message="hello")]),
        scoring=LLMJudgeScoring(method="llm_judge", rubric="rate it", pass_threshold=4),
    )

    result = await runner.run_case(case)

    assert result.passed
    assert result.score == 5.0
    assert "under-test-model" in calls
    assert "judge-model-x" in calls


def test_judge_model_defaults_to_llm_model_when_unset():
    runner = EvalRunner(_settings(llm_model="my-model", judge_model=None))

    assert runner._llm_adapter._model == "my-model"
    assert runner._judge_llm_adapter._model == "my-model"


async def test_multi_turn_case_runs_every_turn_but_scores_only_the_last(monkeypatch):
    calls = []

    async def tracking_chat_completion(self, messages):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": "Prefers email over phone calls.", "usage": None}
        return {"content": "You should reach out by email.", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    runner = EvalRunner(_settings())
    case = EvalCase(
        id="multi-turn-case",
        intent="chat",
        input=CaseInput(
            turns=[
                CaseTurn(session_id="s-write", message="remember that I prefer email over phone calls"),
                CaseTurn(session_id="s-read", message="how should I be contacted?"),
            ]
        ),
        scoring=RuleScoring(method="rule", function="contains_any", args={"field": "reply", "expected": ["email"]}),
    )

    result = await runner.run_case(case)

    assert len(calls) == 2  # both turns actually ran
    assert result.output["reply"] == "You should reach out by email."  # only the LAST turn is scored
    assert result.passed
    # The second turn's LLM call must actually have the first turn's
    # remembered fact folded into it (Phase 5a's memory read) — not just
    # coincidentally pass because both fakes mention "email".
    second_call_messages = calls[1]
    assert any("Prefers email over phone calls." in m["content"] for m in second_call_messages)


async def test_run_case_unsupported_intent_fails_cleanly_not_raises():
    runner = EvalRunner(_settings())
    case = EvalCase(
        id="bad-intent-case",
        intent="not-a-real-intent",
        input=CaseInput(turns=[CaseTurn(message="hi")]),
        scoring=RuleScoring(method="rule", function="field_non_empty", args={"field": "reply"}),
    )

    result = await runner.run_case(case)

    assert not result.passed
    assert result.error is not None


async def test_run_case_pipeline_exception_is_caught_not_raised(monkeypatch):
    async def broken_chat_completion(self, messages):
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", broken_chat_completion)

    runner = EvalRunner(_settings())
    case = EvalCase(
        id="broken-case",
        intent="chat",
        input=CaseInput(turns=[CaseTurn(message="hi")]),
        scoring=RuleScoring(method="rule", function="field_non_empty", args={"field": "reply"}),
    )

    result = await runner.run_case(case)

    assert not result.passed
    assert result.error is not None


async def test_run_all_aggregates_pass_and_fail_counts(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {"content": "a reply", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    runner = EvalRunner(_settings())
    passing_case = EvalCase(
        id="pass-case",
        intent="chat",
        input=CaseInput(turns=[CaseTurn(message="hi")]),
        scoring=RuleScoring(method="rule", function="field_non_empty", args={"field": "reply"}),
    )
    failing_case = EvalCase(
        id="fail-case",
        intent="chat",
        input=CaseInput(turns=[CaseTurn(message="hi")]),
        scoring=RuleScoring(method="rule", function="field_non_empty", args={"field": "does_not_exist"}),
    )

    report = await runner.run_all([passing_case, failing_case])

    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1
    assert report.pass_rate == 0.5
    assert len(report.results) == 2
    assert report.run_id  # a correlation id was generated


# --- Report rendering -------------------------------------------------


def test_render_markdown_includes_summary_and_per_case_rows():
    report = build_report(
        "run-1",
        [
            EvalCaseResult(case_id="c1", intent="chat", description=None, passed=True, score=5.0, detail="ok", output={"reply": "hi"}),
            EvalCaseResult(case_id="c2", intent="mail", description=None, passed=False, score=None, detail="missing field", output={}),
        ],
    )

    markdown = render_markdown(report)

    assert "c1" in markdown
    assert "c2" in markdown
    assert "PASS" in markdown
    assert "FAIL" in markdown
    assert "50%" in markdown


def test_render_json_round_trips_report_fields():
    report = build_report(
        "run-1",
        [
            EvalCaseResult(case_id="c1", intent="chat", description="desc", passed=True, score=5.0, detail="ok", output={"reply": "hi"}),
        ],
    )

    payload = json.loads(render_json(report))

    assert payload["run_id"] == "run-1"
    assert payload["total"] == 1
    assert payload["passed"] == 1
    assert payload["results"][0]["case_id"] == "c1"
    assert payload["results"][0]["output"] == {"reply": "hi"}


# --- CLI (app/evals/run.py) ---------------------------------------------


async def test_cli_main_with_no_cases_exits_cleanly(tmp_path, capsys):
    from app.evals.run import main as cli_main

    exit_code = await cli_main(["--cases-dir", str(tmp_path)])

    assert exit_code == 0
    assert "No cases found" in capsys.readouterr().out


async def test_cli_main_runs_cases_and_writes_a_report(tmp_path, monkeypatch, capsys):
    case_yaml = textwrap.dedent(
        """
        cases:
          - id: cli-case
            intent: chat
            input:
              turns:
                - message: "hello"
            scoring:
              method: rule
              function: field_non_empty
              args:
                field: reply
        """
    )
    (tmp_path / "sample.yaml").write_text(case_yaml)

    async def fake_chat_completion(self, messages):
        return {"content": "hi there", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    out_path = tmp_path / "report.md"
    from app.evals.run import main as cli_main

    exit_code = await cli_main(["--cases-dir", str(tmp_path), "--out", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()
    content = out_path.read_text()
    assert "cli-case" in content
    assert "PASS" in content


async def test_cli_main_json_format(tmp_path, monkeypatch):
    case_yaml = textwrap.dedent(
        """
        cases:
          - id: cli-json-case
            intent: chat
            input:
              turns:
                - message: "hello"
            scoring:
              method: rule
              function: field_non_empty
              args:
                field: reply
        """
    )
    (tmp_path / "sample.yaml").write_text(case_yaml)

    async def fake_chat_completion(self, messages):
        return {"content": "hi there", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    out_path = tmp_path / "report.json"
    from app.evals.run import main as cli_main

    exit_code = await cli_main(["--cases-dir", str(tmp_path), "--format", "json", "--out", str(out_path)])

    assert exit_code == 0
    payload = json.loads(out_path.read_text())
    assert payload["results"][0]["case_id"] == "cli-json-case"
