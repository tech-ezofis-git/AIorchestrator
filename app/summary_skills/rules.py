"""Summary rules: LLM instructions from SKILL.md/.mdc; enforcement in Python.

Replaceable packs live under `skills/summary/` (or SUMMARY_SKILL_DIR /
AGENT_SKILLS_ROOT). Deterministic <mark> injection stays here in code.
"""
from __future__ import annotations

import re

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

EMPTY_SUMMARY_TEXT = (
    "I couldn't extract any text from that document, so I can't summarize it."
)

SUMMARY_MAX_SENTENCES = 3
FACTS_MUST_BE_SENTENCES = True
FACTS_FORBID_LABEL_VALUE = True
NO_DUPLICATE_FACTS_VS_SUMMARY = True
REQUIRE_MARK_HIGHLIGHTS = True

USER_PROMPT_PREFIX = (
    "Infer the document type from the OCR text, then summarize "
    "using facts that match that type. Do not assume it is an invoice."
)


def system_prompt(*, settings=None) -> str:
    """LLM system prompt = Summary SKILL.md + rules/*.mdc."""
    return get_skill("summary", settings=settings).system_prompt


def __getattr__(name: str):
    """Lazy SYSTEM_PROMPT for callers/tests that still import the constant."""
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_user_prompt(*, source: str, page_label: str, ocr_text: str) -> str:
    page_bit = f" ({page_label})" if page_label else ""
    return (
        f"Document: {source}{page_bit}\n\n"
        f"{USER_PROMPT_PREFIX}\n\n"
        f"OCR text:\n{ocr_text}"
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


def ocr_highlight_phrases(ocr_text: str) -> list[str]:
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
    return phrases


def _wrap_literal_phrases(segment: str, phrases: list[str]) -> str:
    if not segment or not phrases:
        return segment
    out = segment
    for phrase in sorted({p for p in phrases if p}, key=len, reverse=True):
        pieces: list[str] = []
        for part in _MARK_SEGMENT_RE.split(out):
            if not part:
                continue
            if _is_mark_segment(part) or phrase.casefold() not in part.casefold():
                pieces.append(part)
                continue
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            pieces.append(
                pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", part, count=1)
            )
        out = "".join(pieces)
    return out


def _wrap_highlight_patterns(segment: str) -> str:
    if not segment:
        return segment
    out = segment
    for pattern in _HIGHLIGHT_PATTERNS:
        pieces: list[str] = []
        for part in _MARK_SEGMENT_RE.split(out):
            if not part:
                continue
            if _is_mark_segment(part):
                pieces.append(part)
                continue
            pieces.append(pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", part))
        out = "".join(pieces)
    return out


def highlight_summary_text(text: str, *, ocr_text: str = "") -> str:
    """Code rule: important spans must be wrapped in <mark>…</mark>."""
    if not REQUIRE_MARK_HIGHLIGHTS:
        return str(text or "")
    raw = str(text or "")
    if not raw.strip() or raw.strip() == EMPTY_SUMMARY_TEXT:
        return raw
    phrases = ocr_highlight_phrases(ocr_text)
    rebuilt: list[str] = []
    for part in _MARK_SEGMENT_RE.split(raw):
        if not part:
            continue
        if _is_mark_segment(part):
            rebuilt.append(part)
            continue
        with_phrases = _wrap_literal_phrases(part, phrases)
        for sub in _MARK_SEGMENT_RE.split(with_phrases):
            if not sub:
                continue
            if _is_mark_segment(sub):
                rebuilt.append(sub)
            else:
                rebuilt.append(_wrap_highlight_patterns(sub))
    return "".join(rebuilt)


def apply_highlight_rules(
    *,
    document_summary: str,
    key_facts_extracted: list,
    ocr_text: str,
) -> tuple[str, list[str]]:
    summary = highlight_summary_text(document_summary, ocr_text=ocr_text)
    facts = [
        highlight_summary_text(str(fact), ocr_text=ocr_text)
        for fact in (key_facts_extracted or [])
        if str(fact).strip()
    ]
    return summary, facts
