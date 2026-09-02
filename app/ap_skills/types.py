"""AP skill types, ids, and shared helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

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


class ApRunInProgressError(ApSkillError):
    """Raised when a document job targets a (tenant_id, item_key) that
    already has a "running" ap_runs row — a genuinely concurrent duplicate
    submission (a retry that arrived while the first attempt is still in
    flight), not merely a sequential retry after completion (see
    ApSkillRunner.run's dedupe-window short-circuit for that case
    instead). Safe to surface as HTTP 409 — see app/main.py."""


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
    # Frozen LLMAdapter.chat_completion(**overrides) dicts resolved once by
    # app/main.py's chat() handler (tenant/explicit model, or a snapshot of
    # the adapter's current default) — never None once ApSkillRunner builds
    # this context. Every ctx.llm.chat_completion(...) call in this package
    # MUST pass `**(ctx.llm_overrides or {})` (or the fallback dict on
    # retry) rather than relying on the shared adapter's ambient state; see
    # app/catalog/tenant_llm.py's module docstring for why.
    llm_overrides: Optional[dict[str, Any]] = None
    llm_fallback_overrides: Optional[dict[str, Any]] = None


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


def norm_token(value: str) -> str:
    """Lowercased, alnum-only normalization — the shared primitive several
    ap_skills modules each used to hand-roll their own copy of (ultrareview
    reuse fix): duplicate_detect/finalize_decision use it via `field_text`
    below, and extract_invoice's LLM-groundedness check (finding #6)
    imports it directly instead of re-defining it a fourth time."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


_PLACEHOLDER_VALUES = frozenset(
    {
        "terms",
        "currency",
        "ponumber",
        "invoiceno",
        "invoicenumber",
        "vendorname",
        "vendor",
        "supplier",
        "matchedstatus",
        "documenttype",
        "invoicedate",
        "duedate",
        "invoiceamount",
        "buyer",
        "shiptoaddress",
        "invoice",
        "number",
        "na",
        "none",
        "null",
    }
)


def field_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        token = norm_token(text)
        if token in _PLACEHOLDER_VALUES or token == norm_token(key):
            continue
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


DEFAULT_LINE_MATCH_FLOOR = 0.5


def match_lines_by_description(
    left_lines: list[dict[str, Any]],
    right_lines: list[dict[str, Any]],
    *,
    describe: Callable[[dict[str, Any]], str],
    floor: float = DEFAULT_LINE_MATCH_FLOOR,
) -> list[Optional[int]]:
    """Best `right_lines` index for each `left_lines` entry, by
    `name_similarity` on `describe(line)`. Returns a list the same length
    as `left_lines`; each entry is a `right_lines` index (each used at
    most once) or None if nothing scored at/above `floor`.

    Shared by backorder_detect.py (PO lines vs invoice lines) and
    grn_match.py (invoice lines vs GRN lines) — extracted here
    (ultrareview altitude fix) after both were found to have the same
    bug: matching two line-item lists purely by array position silently
    mispairs them whenever the two lists don't happen to list items in
    the same order, which is common (invoices/GRNs don't have to mirror
    PO line order). Falls back to position only when NEITHER list has a
    usable description anywhere to compare against, so a plain/unlabeled
    line list still gets a best-effort match rather than none.
    """
    has_any_description = any(describe(line) for line in right_lines)
    used: set[int] = set()
    matches: list[Optional[int]] = []
    for position, left_line in enumerate(left_lines):
        left_desc = describe(left_line)
        if not has_any_description or not left_desc:
            matches.append(position if position < len(right_lines) and position not in used else None)
            if position < len(right_lines):
                used.add(position)
            continue
        best_index: Optional[int] = None
        best_score = 0.0
        for index, right_line in enumerate(right_lines):
            if index in used:
                continue
            score = name_similarity(left_desc, describe(right_line))
            if score > best_score:
                best_index, best_score = index, score
        # best_index and best_score are always assigned together above, and
        # best_score starts below `floor` — so best_score >= floor already
        # implies best_index is not None.
        if best_score >= floor:
            used.add(best_index)
            matches.append(best_index)
        else:
            matches.append(None)
    return matches
