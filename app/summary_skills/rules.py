"""Summary rules: LLM instructions from SKILL.md/.mdc; enforcement in Python.

Replaceable packs live under `skills/summary/` (or SUMMARY_SKILL_DIR /
AGENT_SKILLS_ROOT). Deterministic <mark> injection stays here in code.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.agent_skills.loader import get_skill

# --- Locked output contract (code-side constants) -------------------------
SUMMARY_JSON_KEYS: tuple[str, ...] = (
    "confidence_score",
    "document_type",
    "document_title",
    "document_language",
    "document_summary",
    "key_facts_extracted",
    "ocr_text",
)

FORBIDDEN_SUMMARY_KEYS: tuple[str, ...] = (
    "compliance_and_risk_assessment",
    "ai_recommendations",
    "supplier_trend_insight",
)

SUMMARY_JSON_CONTROL_KEYS: frozenset[str] = frozenset({"no", "key_facts_count"})

EMPTY_SUMMARY_TEXT = (
    "I couldn't extract any text from that document, so I can't summarize it."
)

DEFAULT_KEY_FACTS_COUNT = 6
MIN_KEY_FACTS_COUNT = 1
MAX_KEY_FACTS_COUNT = 20

SUMMARY_MAX_SENTENCES = 3
FACTS_MUST_BE_SENTENCES = True
FACTS_FORBID_LABEL_VALUE = True
NO_DUPLICATE_FACTS_VS_SUMMARY = True
REQUIRE_MARK_HIGHLIGHTS = True

MAX_MARKS_IN_SUMMARY = 3
MAX_MARKS_PER_FACT = 1
MAX_OCR_PHRASES_FOR_SUMMARY = 2

USER_PROMPT_PREFIX = (
    "Infer the document type from the source data, then summarize "
    "using facts that match that type. Do not assume it is an invoice."
)

USER_PROMPT_PREFIX_JSON = (
    "Infer the document type from the JSON fields, then summarize "
    "using values present in the JSON only. Do not invent parties, dates, IDs, or amounts."
)


def system_prompt(*, settings=None) -> str:
    """LLM system prompt = Summary SKILL.md + rules/*.mdc."""
    return get_skill("summary", settings=settings).system_prompt


def __getattr__(name: str):
    """Lazy SYSTEM_PROMPT for callers/tests that still import the constant."""
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve_key_facts_count(
    *,
    explicit: Optional[int] = None,
    summary_json: Optional[dict[str, Any]] = None,
) -> int:
    """Resolve max key facts: payload.key_facts_count > summary_json.no > default 6."""
    raw: Any = explicit
    if raw is None and summary_json:
        raw = summary_json.get("key_facts_count")
        if raw is None:
            raw = summary_json.get("no")
    if raw is None:
        return DEFAULT_KEY_FACTS_COUNT
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_KEY_FACTS_COUNT
    return max(MIN_KEY_FACTS_COUNT, min(MAX_KEY_FACTS_COUNT, count))


def strip_summary_control_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in SUMMARY_JSON_CONTROL_KEYS}


def format_structured_payload(data: Any) -> str:
    """Pretty-print arbitrary JSON for the LLM user message."""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(data)


def build_user_prompt(
    *,
    source: str,
    page_label: str = "",
    content: str,
    content_kind: str = "text",
    key_facts_count: int = DEFAULT_KEY_FACTS_COUNT,
) -> str:
    kind = (content_kind or "text").strip().lower()
    prefix = USER_PROMPT_PREFIX_JSON if kind == "json" else USER_PROMPT_PREFIX
    label = "JSON data" if kind == "json" else "OCR text"
    page_bit = f" ({page_label})" if page_label else ""
    facts_line = (
        f"Return at most {key_facts_count} items in key_facts_extracted "
        f"(fewer if the source has less material)."
    )
    return (
        f"Document: {source}{page_bit}\n\n"
        f"{prefix}\n\n"
        f"{facts_line}\n\n"
        f"{label}:\n{content}"
    )


# --- Highlight enforcement (deterministic; model may omit <mark>) ---------
_MARK_SEGMENT_RE = re.compile(r"(<mark>.*?</mark>)", re.IGNORECASE | re.DOTALL)
_OCR_LABEL_VALUE_RE = re.compile(
    r"(?im)^(?:\s*(?:insurer|insured|policyholder|issuer|vendor|seller|buyer|"
    r"customer|supplier|from|to|company|client|payee|payer)\s*[:\-]\s*)(.+)$"
)
_HIGHLIGHT_PATTERNS = (
    re.compile(
        r"\b(?:INR|USD|EUR|GBP|AED|SAR)\s*[\d,]+(?:\.\d+)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z]{1,10}[-/][A-Z0-9][A-Z0-9\-/]{2,}\b"),
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s*%"),
    re.compile(r"\b\d+\s+hours?\b", re.IGNORECASE),
)


def _is_mark_segment(part: str) -> bool:
    lowered = part.lower()
    return lowered.startswith("<mark>") and lowered.endswith("</mark>")


def _count_marks(text: str) -> int:
    return len(_MARK_SEGMENT_RE.findall(text or ""))


def _strip_mark_tags(text: str) -> str:
    return _MARK_SEGMENT_RE.sub(lambda m: m.group(1)[6:-7], text)


def _cap_marks(text: str, max_marks: int) -> str:
    if max_marks < 0 or _count_marks(text) <= max_marks:
        return text
    kept = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal kept
        if kept < max_marks:
            kept += 1
            return match.group(0)
        return _strip_mark_tags(match.group(0))

    return _MARK_SEGMENT_RE.sub(repl, text)


def ocr_highlight_phrases(ocr_text: str, *, limit: Optional[int] = None) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for match in _OCR_LABEL_VALUE_RE.finditer(ocr_text or ""):
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        value = value.rstrip(" ,;")
        if len(value) < 3 or len(value) > 120:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        phrases.append(value)
        if limit is not None and len(phrases) >= limit:
            break
    return phrases


def _wrap_literal_phrases(
    segment: str,
    phrases: list[str],
    *,
    max_injections: Optional[int] = None,
) -> str:
    if not segment or not phrases:
        return segment
    injections = 0
    out = segment
    for phrase in sorted({p for p in phrases if p}, key=len, reverse=True):
        if max_injections is not None and injections >= max_injections:
            break
        pieces: list[str] = []
        for part in _MARK_SEGMENT_RE.split(out):
            if not part:
                continue
            if _is_mark_segment(part) or phrase.casefold() not in part.casefold():
                pieces.append(part)
                continue
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            new_part = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", part, count=1)
            if new_part != part:
                injections += 1
            pieces.append(new_part)
        out = "".join(pieces)
    return out


def _wrap_highlight_patterns(segment: str, *, max_injections: Optional[int] = None) -> str:
    if not segment:
        return segment
    injections = 0
    out = segment
    for pattern in _HIGHLIGHT_PATTERNS:
        if max_injections is not None and injections >= max_injections:
            break
        pieces: list[str] = []
        for part in _MARK_SEGMENT_RE.split(out):
            if not part:
                continue
            if _is_mark_segment(part):
                pieces.append(part)
                continue

            def sub_once(match: re.Match[str]) -> str:
                nonlocal injections
                if max_injections is not None and injections >= max_injections:
                    return match.group(0)
                injections += 1
                return f"<mark>{match.group(0)}</mark>"

            pieces.append(pattern.sub(sub_once, part))
        out = "".join(pieces)
    return out


def highlight_summary_text(
    text: str,
    *,
    ocr_text: str = "",
    max_marks: int = MAX_MARKS_IN_SUMMARY,
    inject_patterns: bool = False,
    inject_phrases: bool = True,
    max_phrase_injections: Optional[int] = None,
    max_pattern_injections: Optional[int] = None,
) -> str:
    """Code rule: lightly wrap important spans in <mark>…</mark>."""
    if not REQUIRE_MARK_HIGHLIGHTS:
        return str(text or "")
    raw = str(text or "")
    if not raw.strip() or raw.strip() == EMPTY_SUMMARY_TEXT:
        return raw

    # Keep model-provided marks, trimmed to the cap.
    capped = _cap_marks(raw, max_marks)
    if _count_marks(capped) >= max_marks:
        return capped

    remaining = max_marks - _count_marks(capped)
    phrases = ocr_highlight_phrases(ocr_text, limit=max_phrase_injections)
    rebuilt: list[str] = []
    for part in _MARK_SEGMENT_RE.split(capped):
        if not part:
            continue
        if _is_mark_segment(part):
            rebuilt.append(part)
            continue
        segment = part
        if inject_phrases and phrases and remaining > 0:
            segment = _wrap_literal_phrases(
                segment,
                phrases,
                max_injections=min(remaining, max_phrase_injections or remaining),
            )
            remaining = max_marks - _count_marks("".join(rebuilt) + segment)
        if inject_patterns and remaining > 0:
            for sub in _MARK_SEGMENT_RE.split(segment):
                if not sub:
                    continue
                if _is_mark_segment(sub):
                    rebuilt.append(sub)
                else:
                    rebuilt.append(
                        _wrap_highlight_patterns(
                            sub,
                            max_injections=min(remaining, max_pattern_injections or remaining),
                        )
                    )
            continue
        rebuilt.append(segment)
    return _cap_marks("".join(rebuilt), max_marks)


def apply_highlight_rules(
    *,
    document_summary: str,
    key_facts_extracted: list,
    ocr_text: str,
) -> tuple[str, list[str]]:
    summary = highlight_summary_text(
        document_summary,
        ocr_text=ocr_text,
        max_marks=MAX_MARKS_IN_SUMMARY,
        inject_patterns=False,
        inject_phrases=True,
        max_phrase_injections=MAX_OCR_PHRASES_FOR_SUMMARY,
    )
    facts = [
        highlight_summary_text(
            str(fact),
            ocr_text=ocr_text,
            max_marks=MAX_MARKS_PER_FACT,
            inject_patterns=True,
            inject_phrases=True,
            max_phrase_injections=1,
            max_pattern_injections=1,
        )
        for fact in (key_facts_extracted or [])
        if str(fact).strip()
    ]
    return summary, facts
