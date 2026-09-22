"""Dynamic prompt generator for Report Agent based on real discovered schema."""
from __future__ import annotations

from app.models.report_agent import DiscoveredField, MissingField
from app.report_agent.templates import TemplateDefinition


def generate_dynamic_prompt(
    template: TemplateDefinition,
    discovered_tables: list[str],
    discovered_fields: list[DiscoveredField],
    missing_fields: list[MissingField],
) -> str:
    """Generate a strict, hallucination-free prompt referencing real database fields."""
    lines: list[str] = [
        "You are the Report Agent for EZOFIS, an enterprise document and workflow platform.",
        "",
        "Create a report for:",
        "",
        "Title:",
        template.title,
        "",
        "Description:",
        template.description,
        "",
        "Domain:",
        template.domain,
        "",
    ]

    # Group discovered fields by table
    fields_by_table: dict[str, list[DiscoveredField]] = {}
    for f in discovered_fields:
        fields_by_table.setdefault(f.table, []).append(f)

    if fields_by_table:
        lines.append("Database schema discovered:")
        lines.append("")
        for table_name, fields in fields_by_table.items():
            lines.append(f"Table: {table_name}")
            lines.append("Fields:")
            for col in fields:
                lines.append(f"- {col.column} ({col.type})")
            lines.append("")
    else:
        lines.append("Database schema discovered: No relevant database fields were found.")
        lines.append("")

    lines.append("Report Requirements:")
    lines.append(f"1. Generate a report for '{template.title}' fulfilling the business purpose: {template.description}")
    lines.append("2. Select relevant identifier, status, date, and metric columns from the discovered database tables.")
    lines.append("")

    lines.append("Instructions:")
    lines.append("1. Use the discovered database fields above to determine the fields required for the report.")
    lines.append("2. Use ONLY fields that actually exist in the discovered schema.")
    lines.append("3. Do NOT invent, assume, or hallucinate database column names.")
    lines.append("4. Identify the primary identifier and filter columns from the discovered tables.")
    lines.append("5. If multi-table aggregation or joins are needed, specify the matching key columns.")

    if missing_fields:
        lines.append("")
        lines.append("Missing Field Warnings:")
        for mf in missing_fields:
            lines.append(
                f"- {mf.field}: {mf.reason} Do not invent one. Determine whether this value can be derived or calculated from available fields; if not, mark it as unavailable."
            )

    return "\n".join(lines).strip()
