"""backorder_detect — invoice qty vs PO qty (short-ship). Skipped unless tenant-enabled."""
from __future__ import annotations

from app.ap_skills.types import (
    ApContext,
    ApSkillError,
    ApSkillResult,
    field_number,
    field_text,
    invoice_from,
    match_lines_by_description,
)

SKILL_ID = "backorder_detect"


def _qty(line: dict) -> float:
    value = field_number(line, "qty", "quantity")
    return float(value) if value is not None else 0.0


def _description(line: dict) -> str:
    return field_text(line, "description", "item", "name", "Description")


async def run(ctx: ApContext) -> ApSkillResult:
    po_match = ctx.artifacts.get("po_match")
    if not isinstance(po_match, dict) or not po_match:
        raise ApSkillError(
            "backorder_detect requires a stored po_match artifact. Run po_match first."
        )
    if po_match.get("decision") == "NOT_MATCHED" or not po_match.get("po"):
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "detected": False,
                "missing_qty_by_item": [],
                "recommendation": "NO_ACTION",
                "reason": "No established PO match; back-order check skipped.",
            },
        )

    invoice = invoice_from(ctx)
    raw_inv_lines = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    inv_lines = [line for line in raw_inv_lines if isinstance(line, dict)]
    po = po_match.get("po") or {}
    raw_po_lines = po.get("lines") if isinstance(po.get("lines"), list) else []
    po_lines = [line for line in raw_po_lines if isinstance(line, dict)]

    matched_indexes = match_lines_by_description(po_lines, inv_lines, describe=_description)

    missing = []
    for position, (po_line, inv_index) in enumerate(zip(po_lines, matched_indexes)):
        po_qty = _qty(po_line)
        if inv_index is None:
            # Code-review finding #17: a PO line with no matching invoice
            # line at all is a distinct case from a line that's present
            # but short — flagged `missing_line` rather than silently
            # treated as an invoice_qty of 0 indistinguishable from a
            # genuine (data-confirmed) zero-quantity short-ship.
            # (ultrareview fix: only when po_qty > 0 — a PO line with no
            # quantity at all, or an explicit 0, has nothing outstanding
            # regardless of whether an invoice line was matched to it, and
            # must not be counted as a backorder.)
            if po_qty <= 0:
                continue
            missing.append(
                {
                    "po_line_id": po_line.get("id") or str(position),
                    "description": _description(po_line),
                    "po_qty": po_qty,
                    "invoice_qty": None,
                    "remaining": po_qty,
                    "missing_line": True,
                    "reason": "NO_MATCHING_INVOICE_LINE",
                }
            )
            continue
        inv_qty = _qty(inv_lines[inv_index])
        remaining = round(po_qty - inv_qty, 4)
        if remaining > 0:
            missing.append(
                {
                    "po_line_id": po_line.get("id") or str(position),
                    "description": _description(po_line),
                    "po_qty": po_qty,
                    "invoice_qty": inv_qty,
                    "remaining": remaining,
                    "missing_line": False,
                    "reason": "SHORT_SHIP",
                }
            )

    detected = bool(missing)
    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "detected": detected,
            "missing_qty_by_item": missing,
            "recommendation": "WAIT_FOR_BALANCE" if detected else "NO_ACTION",
            "reason": (
                f"{len(missing)} PO line(s) still short."
                if detected
                else "Invoice covers PO quantities."
            ),
        },
    )
