"""vendor_validate — invoice vendor vs PO vendor or vendor master. Not always-on."""
from __future__ import annotations

from app.ap_skills.types import (
    ApContext,
    ApSkillResult,
    field_text,
    invoice_from,
    name_similarity,
)

SKILL_ID = "vendor_validate"


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    vendor = field_text(invoice, "vendor", "supplier", "vendor_name")
    if not vendor:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "status": "MISSING",
                "vendor": None,
                "expected": None,
                "match_score": 0,
                "reason": "No vendor name found on the invoice.",
            },
        )

    expected = None
    po_match = ctx.artifacts.get("po_match") or {}
    po = po_match.get("po") if isinstance(po_match, dict) else None
    if isinstance(po, dict):
        expected = field_text(po, "vendor", "supplier", "Vendor Name", "Vendor")

    if not expected:
        master = await ctx.ezofis.lookup_vendor(tenant_id=ctx.tenant_id, vendor_name=vendor)
        if isinstance(master, dict):
            expected = field_text(master, "name", "vendor", "vendor_name") or vendor
            status = str(master.get("status") or "ACTIVE").upper()
            return ApSkillResult(
                skill_id=SKILL_ID,
                data={
                    "status": status,
                    "vendor": vendor,
                    "expected": expected,
                    "match_score": round(name_similarity(vendor, expected) * 100, 2),
                    "source": "vendor_master",
                    "reason": f"Vendor master status {status}.",
                },
            )

    if expected:
        sim = name_similarity(vendor, expected)
        matched = sim >= 0.85
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "status": "ACTIVE" if matched else "MISMATCH",
                "vendor": vendor,
                "expected": expected,
                "match_score": round(sim * 100, 2),
                "source": "po",
                "reason": (
                    "Invoice vendor matches PO vendor."
                    if matched
                    else f"Invoice vendor '{vendor}' does not match PO vendor '{expected}'."
                ),
            },
        )

    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "status": "ACTIVE",
            "vendor": vendor,
            "expected": None,
            "match_score": 100,
            "source": "heuristic",
            "reason": "No PO or vendor master available; vendor name looks usable.",
        },
    )
