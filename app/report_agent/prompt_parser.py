"""Prompt parser for Report Agent — extracts template title, discovered tables, and fields from prompt text."""
from __future__ import annotations

import logging
import re
from typing import Optional
from app.models.report_agent import DiscoveredField

logger = logging.getLogger("orchestrator.report_agent.prompt_parser")


def parse_prompt_metadata(prompt: str) -> tuple[Optional[str], list[str], list[DiscoveredField]]:
    """Parse template title, discovered tables, and fields from a generated prompt text.

    Returns:
        tuple[Optional[str], list[str], list[DiscoveredField]]:
        (template_title, discovered_tables, discovered_fields)
    """
    if not prompt or not prompt.strip():
        return None, [], []

    cleaned = prompt.strip()

    # 1. Extract Title: "Title:\n<title>" or "Create a report for:\n\nTitle:\n<title>"
    title = None
    title_match = re.search(r"\bTitle:\s*\n?\s*([^\n]+)", cleaned, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()

    # 2. Extract Tables and Fields under "Database schema discovered:"
    tables: list[str] = []
    fields: list[DiscoveredField] = []

    # Split by "Table: "
    table_sections = re.split(r"(?i)\bTable:\s*", cleaned)
    for section in table_sections[1:]:
        lines = [line.strip() for line in section.strip().split("\n") if line.strip()]
        if not lines:
            continue
        table_name = lines[0].strip().strip(" :")
        if not table_name:
            continue
        if table_name not in tables:
            tables.append(table_name)

        # Parse field lines: "- <col_name> (<data_type>)"
        for line_str in lines[1:]:
            if line_str.startswith("-"):
                field_match = re.match(r"^-\s*([a-zA-Z0-9_]+)\s*\(([^)]+)\)", line_str)
                if field_match:
                    col_name = field_match.group(1).strip()
                    data_type = field_match.group(2).strip()
                    fields.append(
                        DiscoveredField(
                            table=table_name,
                            column=col_name,
                            type=data_type,
                        )
                    )
            elif any(
                line_str.lower().startswith(kw)
                for kw in ("table:", "report requirements:", "instructions:", "missing field warnings:")
            ):
                break

    logger.debug(
        "parsed_prompt_metadata",
        extra={"title": title, "tables_count": len(tables), "fields_count": len(fields)},
    )
    return title, tables, fields
