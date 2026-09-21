"""Seed sample tenant customs for Summary / OCR / Insight / Prompt / PDF (EZOFIS)."""
from __future__ import annotations

import argparse
import asyncio
import uuid

import asyncpg

EZOFIS_TENANT = "b843b988-00ec-44e3-aca2-b8470133ef63"

# agent -> {skills, rules}
SAMPLES = {
    "summary": {
        "skills": [
            {
                "slug": "custom1",
                "source_file": "custom1.md",
                "body": (
                    "# EZOFIS tenant Summary emphasis\n\n"
                    "- Name the main parties and the document purpose in the narrative.\n"
                    "- Prefer concrete IDs and amounts already present in the OCR text.\n"
                    "- Do not invent fields that are not in the source text.\n"
                ),
            },
        ],
        "rules": [
            {
                "slug": "custom1",
                "source_file": "custom1.mdc",
                "description": "Highlight PO / invoice IDs for EZOFIS UI",
                "body": (
                    "# Highlights (tenant)\n\n"
                    "- Prefer `<b><u>...</u></b>` on PO numbers, invoice numbers, and totals "
                    "when they appear in the source.\n"
                    "- Keep at most a few highlighted spans; do not highlight every label.\n"
                ),
            },
            {
                "slug": "custom2",
                "source_file": "custom2.mdc",
                "description": "Do not invent fields; keep tone factual",
                "body": (
                    "# Factual tone (tenant)\n\n"
                    "- Stay factual and concise; no marketing language.\n"
                    "- If a value is missing in OCR, leave it empty rather than guessing.\n"
                ),
            },
        ],
    },
    "ocr": {
        "skills": [
            {
                "slug": "custom1",
                "source_file": "custom1.md",
                "body": (
                    "# EZOFIS tenant OCR emphasis\n\n"
                    "- Prefer vendor / bill-from identity fields when present.\n"
                    "- Keep DATE values as YYYY-MM-DD when normalization is confident.\n"
                ),
            },
        ],
        "rules": [
            {
                "slug": "custom1",
                "source_file": "custom1.mdc",
                "description": "Prefer invoice + PO identifiers",
                "body": (
                    "# Field priority (tenant)\n\n"
                    "- Prefer invoice number and PO number when both appear.\n"
                    "- Do not invent GSTIN / tax IDs that are not visible in the page text.\n"
                ),
            },
        ],
    },
    "insight": {
        "skills": [
            {
                "slug": "custom1",
                "source_file": "custom1.md",
                "body": (
                    "# EZOFIS tenant Insight emphasis\n\n"
                    "- Lead with actionable findings tied to the report data.\n"
                    "- Call out anomalies with the concrete metric values from the payload.\n"
                ),
            },
        ],
        "rules": [
            {
                "slug": "custom1",
                "source_file": "custom1.mdc",
                "description": "Keep insights concise",
                "body": (
                    "# Insight tone (tenant)\n\n"
                    "- Prefer 3–5 crisp bullets over long paragraphs.\n"
                    "- Do not invent metrics that are absent from the report payload.\n"
                ),
            },
        ],
    },
    "prompt": {
        "skills": [
            {
                "slug": "custom1",
                "source_file": "custom1.md",
                "body": (
                    "# EZOFIS tenant Prompt emphasis\n\n"
                    "- Follow the user’s requested output shape exactly.\n"
                    "- Prefer structured JSON when the user asks for fields or tables.\n"
                ),
            },
        ],
        "rules": [
            {
                "slug": "custom1",
                "source_file": "custom1.mdc",
                "description": "Passthrough without inventing context",
                "body": (
                    "# Prompt contract (tenant)\n\n"
                    "- Do not invent business facts not present in the user prompt.\n"
                    "- If the request is underspecified, ask for the missing constraint briefly.\n"
                ),
            },
        ],
    },
    "pdf": {
        "skills": [
            {
                "slug": "custom1",
                "source_file": "custom1.md",
                "body": (
                    "# EZOFIS tenant PDF emphasis\n\n"
                    "- Keep generated PDF structure aligned with the selected template.\n"
                    "- Prefer clear labels for amounts, parties, and dates.\n"
                ),
            },
        ],
        "rules": [],
    },
}


async def upsert_skill(
    conn: asyncpg.Connection, *, tenant_id: str, agent: str, sample: dict
) -> str:
    row = await conn.fetchrow(
        "SELECT id FROM tenant_agent_skills WHERE tenant_id=$1 AND agent_slug=$2 AND slug=$3",
        tenant_id,
        agent,
        sample["slug"],
    )
    item_id = str(row["id"]) if row else str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO tenant_agent_skills
          (id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, TRUE, $7)
        ON CONFLICT (tenant_id, agent_slug, slug) DO UPDATE SET
          body = EXCLUDED.body,
          source_file = EXCLUDED.source_file,
          is_active = TRUE,
          updated_by = EXCLUDED.updated_by,
          updated_at = now()
        """,
        item_id,
        tenant_id,
        agent,
        sample["slug"],
        sample["source_file"],
        sample["body"].strip(),
        "seed-all-agents",
    )
    return sample["slug"]


async def upsert_rule(
    conn: asyncpg.Connection, *, tenant_id: str, agent: str, sample: dict
) -> str:
    row = await conn.fetchrow(
        "SELECT id FROM tenant_agent_rules WHERE tenant_id=$1 AND agent_slug=$2 AND slug=$3",
        tenant_id,
        agent,
        sample["slug"],
    )
    item_id = str(row["id"]) if row else str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO tenant_agent_rules
          (id, tenant_id, agent_slug, slug, source_file, description, body, always_apply, is_active, updated_by)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, TRUE, TRUE, $8)
        ON CONFLICT (tenant_id, agent_slug, slug) DO UPDATE SET
          body = EXCLUDED.body,
          source_file = EXCLUDED.source_file,
          description = EXCLUDED.description,
          always_apply = TRUE,
          is_active = TRUE,
          updated_by = EXCLUDED.updated_by,
          updated_at = now()
        """,
        item_id,
        tenant_id,
        agent,
        sample["slug"],
        sample["source_file"],
        sample.get("description"),
        sample["body"].strip(),
        "seed-all-agents",
    )
    return sample["slug"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dsn",
        default="postgresql://orchestrator:orchestrator@127.0.0.1:55432/orchestrator",
    )
    parser.add_argument("--tenant-id", default=EZOFIS_TENANT)
    args = parser.parse_args()

    conn = await asyncpg.connect(args.dsn)
    try:
        await conn.execute(
            "ALTER TABLE tenant_agent_rules ADD COLUMN IF NOT EXISTS description TEXT"
        )
        for agent, pack in SAMPLES.items():
            skills = [
                await upsert_skill(conn, tenant_id=args.tenant_id, agent=agent, sample=s)
                for s in pack.get("skills") or []
            ]
            rules = [
                await upsert_rule(conn, tenant_id=args.tenant_id, agent=agent, sample=r)
                for r in pack.get("rules") or []
            ]
            print(agent, "skills", skills, "rules", rules)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
