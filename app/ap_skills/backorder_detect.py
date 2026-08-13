"""backorder_detect — invoice qty vs PO qty (short-ship). Skipped unless tenant-enabled."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillError, ApSkillResult, field_number, invoice_from

SKILL_ID = "backorder_detect"


def _qty(line: dict) -> float:
    value = field_number(line, "qty", "quantity")
    return float(value) if value is not None else 0.0


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
    inv_lines = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    po = po_match.get("po") or {}
    po_lines = po.get("lines") if isinstance(po.get("lines"), list) else []

    missing = []
    for index, po_line in enumerate(po_lines):
        if not isinstance(po_line, dict):
            continue
        po_qty = _qty(po_line)
        inv_qty = 0.0
        if index < len(inv_lines) and isinstance(inv_lines[index], dict):
            inv_qty = _qty(inv_lines[index])
        remaining = round(po_qty - inv_qty, 4)
        if remaining > 0:
            missing.append(
                {
                    "po_line_id": po_line.get("id") or str(index),
                    "description": po_line.get("description") or "",
                    "po_qty": po_qty,
                    "invoice_qty": inv_qty,
                    "remaining": remaining,
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
