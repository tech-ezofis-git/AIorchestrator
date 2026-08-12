"""Scoring for the eval harness (Phase 5c) — two kinds, funneling into one
`ScoreResult` shape so runner.py doesn't need to know which kind a case
used:

  - Rule-based (`score_rule`): objective, deterministic checks over the
    pipeline's output dict — no LLM call, no network I/O, nothing here
    needs a real API key. See RULE_FUNCTIONS for the registered checks.
  - LLM-judge (`score_llm_judge`): subjective quality, scored 1-5 by a
    real LLM call to JUDGE_MODEL (app/config.py — defaults to LLM_MODEL if
    unset, rule 4) with a case-authored rubric. This is the one part of
    scoring that costs real money and needs a real key — see
    app/evals/runner.py for how the judge adapter is constructed.

Both are exercised by tests/test_eval_harness_mechanics.py using a fake
LLM adapter for score_llm_judge — no real API key needed to prove the
harness mechanics work.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger("orchestrator.evals.scoring")


@dataclass
class ScoreResult:
    """The outcome of scoring one case, regardless of which method scored
    it. `score` is None for rule checks that only have a boolean outcome
    (rule functions still set it to 1.0/0.0 for report aggregation
    convenience) — it's only ever genuinely absent when scoring itself
    couldn't complete (an unknown rule function, a judge call failure, an
    unparseable judge response)."""

    passed: bool
    score: Optional[float]
    detail: str


def _get_field(output: dict[str, Any], field: str) -> Any:
    """Dotted-path field access into a nested result dict, e.g.
    "mail_draft.recipient" or "forecast_result.predicted_values". Returns
    None for any missing/non-dict intermediate step, rather than raising —
    a missing field is a normal, scoreable "this case failed" outcome, not
    a harness bug."""
    value: Any = output
    for part in field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _extract_numbers(text: str) -> list[float]:
    """Pulls every number (optionally comma-grouped, optionally decimal)
    out of free text, e.g. "grow from 1,000 to 1,092.7" -> [1000.0, 1092.7]."""
    return [float(match.replace(",", "")) for match in re.findall(r"-?\d[\d,]*\.?\d*", text)]


# --- Rule-based checks -------------------------------------------------


def field_non_empty(output: dict[str, Any], *, field: str) -> ScoreResult:
    """Passes if `field` (dotted path) is present and truthy — a non-empty
    string, non-empty list, non-zero number, etc."""
    value = _get_field(output, field)
    passed = bool(value)
    return ScoreResult(passed=passed, score=1.0 if passed else 0.0, detail=f"{field} = {value!r}")


def contains_all(output: dict[str, Any], *, field: str, expected: list[str], case_sensitive: bool = False) -> ScoreResult:
    """Passes if every string in `expected` appears as a substring of
    `field`'s (string-coerced) value."""
    value = str(_get_field(output, field) or "")
    haystack = value if case_sensitive else value.lower()
    missing = [e for e in expected if (e if case_sensitive else e.lower()) not in haystack]
    passed = not missing
    detail = "all expected substrings present" if passed else f"missing from {field}: {missing}"
    return ScoreResult(passed=passed, score=1.0 if passed else 0.0, detail=detail)


def contains_any(output: dict[str, Any], *, field: str, expected: list[str], case_sensitive: bool = False) -> ScoreResult:
    """Passes if at least one string in `expected` appears as a substring
    of `field`'s (string-coerced) value."""
    value = str(_get_field(output, field) or "")
    haystack = value if case_sensitive else value.lower()
    found = [e for e in expected if (e if case_sensitive else e.lower()) in haystack]
    passed = bool(found)
    detail = f"found in {field}: {found}" if passed else f"none of {expected} present in {field}"
    return ScoreResult(passed=passed, score=1.0 if passed else 0.0, detail=detail)


def matches_regex(output: dict[str, Any], *, field: str, pattern: str) -> ScoreResult:
    """Passes if `field`'s (string-coerced) value matches `pattern`
    anywhere (re.search, not re.fullmatch) — e.g. an inline citation
    marker like [1], or a validly-formatted email address."""
    value = str(_get_field(output, field) or "")
    matched = re.search(pattern, value) is not None
    detail = f"pattern {pattern!r} {'matched' if matched else 'did not match'} {field} ({value!r})"
    return ScoreResult(passed=matched, score=1.0 if matched else 0.0, detail=detail)


def numbers_from_field_present_in_text(
    output: dict[str, Any], *, text_field: str, numbers_field: str, tolerance: float = 0.5
) -> ScoreResult:
    """Passes if every number in `numbers_field` (a list, dotted path —
    e.g. "forecast_result.predicted_values") is mentioned somewhere in
    `text_field`'s text (dotted path — e.g. "reply"), within `tolerance`.

    Deliberately cross-checks two fields of the SAME output rather than
    comparing against numbers hardcoded in the case YAML — the mocked
    forecast client's numbers are a deterministic hash of the metric text
    (app/integrations/forecast_model.py), so hardcoding expected values in
    a case file would silently break the moment that hash function
    changes. Checking self-consistency between the raw numbers and their
    narration is exactly what "does the narration match the raw numbers"
    (rule 7) means, and it's robust to that implementation detail.
    """
    text = str(_get_field(output, text_field) or "")
    numbers = _get_field(output, numbers_field) or []
    found = _extract_numbers(text)
    missing = [n for n in numbers if not any(abs(float(n) - f) <= tolerance for f in found)]
    passed = bool(numbers) and not missing
    if not numbers:
        detail = f"{numbers_field} was empty — nothing to check"
    elif passed:
        detail = f"all of {numbers_field} mentioned in {text_field}"
    else:
        detail = f"missing from {text_field} (tolerance {tolerance}): {missing}"
    return ScoreResult(passed=passed, score=1.0 if passed else 0.0, detail=detail)


RULE_FUNCTIONS: dict[str, Callable[..., ScoreResult]] = {
    "field_non_empty": field_non_empty,
    "contains_all": contains_all,
    "contains_any": contains_any,
    "matches_regex": matches_regex,
    "numbers_from_field_present_in_text": numbers_from_field_present_in_text,
}


def score_rule(output: dict[str, Any], *, function: str, args: dict[str, Any]) -> ScoreResult:
    """Looks up `function` in RULE_FUNCTIONS and calls it with `output` +
    `args`. An unknown function name or a function that raises is scored
    as a clean failure, never an unhandled exception — a bad case
    definition shouldn't crash an entire eval run."""
    fn = RULE_FUNCTIONS.get(function)
    if fn is None:
        return ScoreResult(passed=False, score=None, detail=f"unknown rule function '{function}'")
    try:
        return fn(output, **args)
    except Exception as exc:
        logger.warning("eval_rule_scoring_failed", extra={"function": function, "error_type": type(exc).__name__})
        return ScoreResult(passed=False, score=None, detail=f"rule function '{function}' raised {type(exc).__name__}: {exc}")


# --- LLM-judge scoring ---------------------------------------------------


class JudgeLLMAdapter(Protocol):
    async def chat_completion(self, messages: list[dict[str, str]]) -> dict: ...


_JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial evaluator for an enterprise AI assistant's "
    "output. Follow the rubric exactly. Respond in EXACTLY this format, "
    "nothing else:\n"
    "Score: <number from 1 to 5>\n"
    "Justification: <one or two sentences>"
)

_SCORE_PATTERN = re.compile(r"Score:\s*([\d.]+)", re.IGNORECASE)
_JUSTIFICATION_PATTERN = re.compile(r"Justification:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_judge_response(content: str) -> tuple[Optional[float], str]:
    """Parses the "Score: <n>\\nJustification: <text>" format the judge is
    asked for. Falls back to the raw content as the detail if the model
    doesn't follow the format — same "don't silently drop information on a
    format miss" discipline as _parse_mail_draft in
    app/core/response_composer.py."""
    score_match = _SCORE_PATTERN.search(content)
    justification_match = _JUSTIFICATION_PATTERN.search(content)
    score = float(score_match.group(1)) if score_match else None
    justification = justification_match.group(1).strip() if justification_match else content.strip()
    return score, justification


async def score_llm_judge(
    *, judge_llm_adapter: JudgeLLMAdapter, rubric: str, output_text: str, pass_threshold: float
) -> ScoreResult:
    """One LLM call to `judge_llm_adapter` (constructed against JUDGE_MODEL
    by the runner, never LLM_MODEL directly — rule 4) per case needing it.
    A judge call failure or an unparseable response is scored as a clean
    failure with the reason in `detail`, never an unhandled exception.
    """
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": f"{rubric}\n\n---\nOUTPUT TO EVALUATE:\n{output_text}"},
    ]
    try:
        result = await judge_llm_adapter.chat_completion(messages)
    except Exception as exc:
        logger.warning("eval_judge_call_failed", extra={"error_type": type(exc).__name__})
        return ScoreResult(passed=False, score=None, detail=f"judge LLM call failed: {type(exc).__name__}: {exc}")

    score, justification = _parse_judge_response(result["content"])
    if score is None:
        return ScoreResult(passed=False, score=None, detail=f"could not parse judge response: {result['content']!r}")

    passed = score >= pass_threshold
    return ScoreResult(passed=passed, score=score, detail=justification)
