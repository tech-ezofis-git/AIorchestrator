"""AP skill types, ids, and shared helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

# Default pipeline when payload.skills is omitted/null.
# Always ends with finalize_decision then workflow_move_next.
PHASE1_SKILL_ORDER: tuple[str, ...] = (
    "extract_invoice",
    "po_match",
    "duplicate_detect",
    "vendor_validate",
    "backorder_detect",
    "finalize_decision",
    "workflow_move_next",
)

# Deterministic order for all known skills. Opt-in skills (QB/Sage/GL/GRN/…)
# run only when listed in payload.skills.
ALL_SKILL_ORDER: tuple[str, ...] = (
    "extract_invoice",
    "po_lookup_quickbooks",
    "po_lookup_sage",
    "po_match",
    "gl_match",
    "grn_match",
    "duplicate_detect",
    "vendor_validate",
    "matter_validate",
    "backorder_detect",
    "finalize_decision",
    "workflow_progress",
    "workflow_move_next",
)

PHASE1_SKILLS = set(PHASE1_SKILL_ORDER)
ALL_SKILLS = set(ALL_SKILL_ORDER)
# Back-compat alias used by runner default plan.
DEFAULT_SKILL_ORDER = PHASE1_SKILL_ORDER


class ApSkillError(ValueError):
    """Fail-closed skill error — safe to surface as HTTP 400."""


@dataclass
class ApSkillResult:
    skill_id: str
    data: dict[str, Any]
    credits: int = 1


@dataclass
class ApContext:
    tenant_id: str
    item_key: str
    run_id: str
    session_id: str
    invoice_json: Optional[dict[str, Any]]
    artifacts: dict[str, Any]
    settings: Any
    ezofis: Any
    llm: Any = None
    dispatcher: Any = None
    store: Any = None
    document_job: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    form_id: Optional[str] = None


class ApSkill(Protocol):
    skill_id: str

    async def run(self, ctx: ApContext) -> ApSkillResult: ...


def require_artifact(ctx: ApContext, skill_id: str) -> dict[str, Any]:
    data = ctx.artifacts.get(skill_id)
    if not isinstance(data, dict) or not data:
        raise ApSkillError(
            f"Skill requires a stored '{skill_id}' artifact. "
            f"Run that skill first or include it in this request."
        )
    return data


def invoice_from(ctx: ApContext) -> dict[str, Any]:
    if ctx.invoice_json:
        return ctx.invoice_json
    extracted = ctx.artifacts.get("extract_invoice") or {}
    invoice = extracted.get("invoice") if isinstance(extracted, dict) else None
    if isinstance(invoice, dict) and invoice:
        return invoice
    raise ApSkillError(
        "Skill requires invoice data. Run extract_invoice first or pass invoice_json."
    )


def field_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def field_number(data: dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        try:
            return float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            continue
    return None


def normalize_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum() or ch.isspace()).strip()


def name_similarity(left: str, right: str) -> float:
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)


def decision_from_score(score: float, *, approved: int, partial: int) -> str:
    if score >= approved:
        return "MATCHED"
    if score >= partial:
        return "PARTIALLY_MATCHED"
    return "NOT_MATCHED"
