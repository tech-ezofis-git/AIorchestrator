"""gl_match — invoice line items ↔ GL account coding (masters via Ezofis)."""
from __future__ import annotations

from app.ap_skills.types import (
    ApContext,
    ApSkillResult,
    decision_from_score,
    field_text,
    invoice_from,
    name_similarity,
)

SKILL_ID = "gl_match"


def _line_gl(line: dict) -> str:
    return field_text(line, "gl_account", "gl", "account", "GL Account", "category")


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    lines = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    gl_master = await ctx.ezofis.lookup_gl_accounts(tenant_id=ctx.tenant_id)
    accounts = []
    if isinstance(gl_master, dict):
        accounts = gl_master.get("accounts") or gl_master.get("items") or []
    elif isinstance(gl_master, list):
        accounts = gl_master
    account_by_code = {}
    account_by_category = {}
    for row in accounts:
        if not isinstance(row, dict):
            continue
        code = field_text(row, "gl_account", "account", "code", "GL Account")
        category = field_text(row, "category", "name", "description")
        if code:
            account_by_code[code.lower()] = row
        if category:
            account_by_category[category.lower()] = row

    mapped = []
    matched = 0
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        desc = field_text(line, "description", "item", "name")
        hinted = _line_gl(line)
        hit = None
        if hinted and hinted.lower() in account_by_code:
            hit = account_by_code[hinted.lower()]
        elif hinted and hinted.lower() in account_by_category:
            hit = account_by_category[hinted.lower()]
        else:
            best_score = 0.0
            for category, row in account_by_category.items():
                score = name_similarity(desc, category)
                if score > best_score:
                    best_score = score
                    hit = row if score >= 0.5 else None
        gl_code = field_text(hit or {}, "gl_account", "account", "code") if hit else ""
        if gl_code:
            matched += 1
        mapped.append(
            {
                "line_index": index,
                "description": desc,
                "gl_account": gl_code or None,
                "category": field_text(hit or {}, "category", "name") or None,
                "matched": bool(gl_code),
            }
        )

    total = max(len(mapped), 1)
    score = round(100.0 * matched / total, 2) if mapped else 0.0
    approved = int(ctx.thresholds.get("approved") or ctx.settings.ap_approved_threshold)
    partial = int(ctx.thresholds.get("partial") or ctx.settings.ap_partial_threshold)
    decision = decision_from_score(score, approved=approved, partial=partial)
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "score": score,
            "decision": decision,
            "mapped_lines": mapped,
            "matched_lines": matched,
            "total_lines": len(mapped),
            "reason": f"Mapped {matched}/{len(mapped)} line(s) to GL accounts.",
        },
    )
