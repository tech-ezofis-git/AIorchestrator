"""matter_validate — invoice Matter ID vs matter master (tenant-optional)."""
from __future__ import annotations

from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from

SKILL_ID = "matter_validate"


def _matter_id(invoice: dict) -> str:
    return field_text(
        invoice,
        "matter_id",
        "matterId",
        "Matter ID",
        "MatterId",
        "matter_no",
        "Matter No",
    )


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    matter_id = _matter_id(invoice)
    if not matter_id:
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "status": "SKIPPED",
                "matter_id": None,
                "client_name": None,
                "reason": "No Matter ID on invoice.",
            },
        )

    matter_master_id = (
        ctx.document_job.get("matter_master_id")
        or ctx.thresholds.get("matter_master_id")
    )
    match = await ctx.ezofis.lookup_matter(
        tenant_id=ctx.tenant_id,
        matter_id=matter_id,
        matter_master_id=matter_master_id,
    )
    if isinstance(match, dict) and match:
        client = field_text(match, "client_name", "client", "name")
        return ApSkillResult(
            skill_id=SKILL_ID,
            data={
                "status": "MATCHED",
                "matter_id": matter_id,
                "client_name": client or None,
                "matter_master_match": match,
                "reason": f"Matter '{matter_id}' matched; client: {client or 'n/a'}.",
            },
        )

    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "status": "NOT_IN_MASTER",
            "matter_id": matter_id,
            "client_name": None,
            "matter_master_match": None,
            "reason": f"Matter ID '{matter_id}' not in Matter List Master; route for manual entry.",
        },
    )
