"""Insight rules: LLM instructions from SKILL.md/.mdc; lock in Python."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.agent_skills.loader import get_skill
from app.summary_skills.rules import highlight_summary_text

INSIGHT_JSON_KEYS: tuple[str, ...] = ("insights", "insights_count", "insight_area", "source_reference")

INSIGHT_JSON_CONTROL_KEYS: frozenset[str] = frozenset(
    {"no", "insights_count", "insight_area", "area", "dashboard"}
)

EMPTY_INSIGHTS: list[str] = []

DEFAULT_INSIGHTS_COUNT = 4
MIN_INSIGHTS_COUNT = 1
MAX_INSIGHTS_COUNT = 20

MAX_MARKS_PER_INSIGHT = 1
REQUIRE_MARK_HIGHLIGHTS = True


def system_prompt(*, settings=None) -> str:
    """LLM system prompt = Insight SKILL.md + rules/*.mdc."""
    return get_skill("insight", settings=settings).system_prompt


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve_insights_count(
    *,
    explicit: Optional[int] = None,
    insight_json: Optional[dict[str, Any]] = None,
) -> int:
    """payload.insights_count > insight_json.no > default 4."""
    raw: Any = explicit
    if raw is None and insight_json:
        raw = insight_json.get("insights_count")
        if raw is None:
            raw = insight_json.get("no")
    if raw is None:
        return DEFAULT_INSIGHTS_COUNT
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INSIGHTS_COUNT
    return max(MIN_INSIGHTS_COUNT, min(MAX_INSIGHTS_COUNT, count))


def resolve_insight_area(
    *,
    explicit: Optional[str] = None,
    insight_json: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Optional dashboard / business area hint for the model."""
    for candidate in (
        explicit,
        (insight_json or {}).get("insight_area"),
        (insight_json or {}).get("area"),
        (insight_json or {}).get("dashboard"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def strip_insight_control_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in INSIGHT_JSON_CONTROL_KEYS}


def build_user_prompt(
    *,
    source: str,
    content: str,
    content_kind: str = "text",
    instruction: Optional[str] = None,
    insights_count: int = DEFAULT_INSIGHTS_COUNT,
    insight_area: Optional[str] = None,
) -> str:
    kind = (content_kind or "text").strip().lower()
    label = "JSON data" if kind == "json" else "Source text"
    hint = (instruction or "").strip()
    hint_block = f"\n\nExtra instruction:\n{hint}" if hint else ""
    area = (insight_area or "").strip()
    area_block = f"\nBusiness area / dashboard context: {area}\n" if area else ""
    count_line = (
        f"Return at most {insights_count} insights "
        f"(fewer if the source has less material)."
    )
    return (
        f"Source: {source}\n"
        f"Content kind: {kind}\n"
        f"{area_block}\n"
        f"{count_line}\n\n"
        f"{label}:\n{content}"
        f"{hint_block}"
    )


def format_structured_payload(data: Any) -> str:
    """Pretty-print arbitrary JSON for the LLM user message."""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(data)


def apply_insight_highlights(insights: list[str], *, source_text: str = "") -> list[str]:
    """Lightly highlight key numbers/IDs in each insight sentence."""
    if not REQUIRE_MARK_HIGHLIGHTS:
        return list(insights)
    highlighted: list[str] = []
    for line in insights:
        text = str(line or "").strip()
        if not text:
            continue
        highlighted.append(
            highlight_summary_text(
                text,
                ocr_text=source_text,
                max_marks=MAX_MARKS_PER_INSIGHT,
                inject_patterns=True,
                inject_phrases=True,
                max_phrase_injections=1,
                max_pattern_injections=1,
            )
        )
    return highlighted
