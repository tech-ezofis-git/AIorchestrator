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

    # 1. Extract Title: "Title:\n<title>" or "Title: <title>"
    title = None
    title_match = re.search(r"\bTitle:\s*\n?\s*([^\n]+)", cleaned, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip().strip('"\'')

    # 2. Extract Tables and Fields under "Database schema discovered:" or "Table: ..."
    tables: list[str] = []
    fields: list[DiscoveredField] = []

    # Split by "Table: "
    table_sections = re.split(r"(?i)\bTable:\s*", cleaned)
    for section in table_sections[1:]:
        lines = [line.strip() for line in section.strip().split("\n") if line.strip()]
        if not lines:
            continue
        table_name = lines[0].strip().strip(" :\"'`")
        if not table_name:
            continue
        if table_name not in tables:
            tables.append(table_name)

        # Parse field lines: "- <col_name> (<data_type>)" or "- <col_name>" or "* <col_name>" or "• <col_name>"
        for line_str in lines[1:]:
            # Check for bullet items
            bullet_match = re.match(r"^[-*•]\s*([a-zA-Z0-9_\.]+)(?:\s*\(([^)]+)\))?", line_str)
            if bullet_match:
                col_name = bullet_match.group(1).strip().split(".")[-1]
                data_type = (bullet_match.group(2) or "text").strip()
                # Check for stop words in col_name like 'Report' or 'Instructions'
                if col_name.lower() in ("report", "instructions", "missing", "note", "table", "fields"):
                    break
                fields.append(
                    DiscoveredField(
                        table=table_name,
                        column=col_name,
                        type=data_type,
                    )
                )
            elif any(
                line_str.lower().startswith(kw)
                for kw in ("table:", "report requirements:", "instructions:", "missing field warnings:", "domain:")
            ):
                break

    logger.debug(
        "parsed_prompt_metadata",
        extra={"title": title, "tables_count": len(tables), "fields_count": len(fields)},
    )
    return title, tables, fields


def compile_prompt_intent(prompt: str, template_title: Optional[str] = None) -> PromptIntent:
    """Compile raw or edited prompt text into structured business intent.

    Extracts requested fields/tables, business filters (status, date ranges, numeric),
    group-by concepts, sort concepts, calculations, and customized title/description
    without executing SQL or blindly trusting client identifiers.
    """
    from app.models.report_agent import BusinessFilter, BusinessSort, PromptIntent

    if not prompt or not prompt.strip():
        return PromptIntent()

    cleaned = prompt.strip()
    title, tables, fields = parse_prompt_metadata(cleaned)
    if not title and template_title:
        title = template_title

    # Extract description if present
    description = None
    desc_match = re.search(r"\bDescription:\s*\n?\s*([^\n]+)", cleaned, re.IGNORECASE)
    if desc_match:
        description = desc_match.group(1).strip().strip('"\'')

    requested_tables: list[str] = list(tables)
    requested_fields: list[str] = []
    requested_filters: list[BusinessFilter] = []
    group_by_concepts: list[str] = []
    sort_concepts: list[BusinessSort] = []
    requested_calculations: list[str] = []
    warnings: list[str] = []

    # 1. Extract requested fields from parsed bullet fields
    for f in fields:
        if f.column and f.column not in requested_fields:
            requested_fields.append(f.column)

    # 2. Extract inline column phrases: "Columns: a, b, c" or "Select fields: a, b" or "Show only a, b"
    inline_cols_match = re.search(
        r"\b(?:columns|fields|select\s+fields|include\s+fields|select\s+columns|include\s+columns|columns\s+to\s+include):\s*([^\n]+)",
        cleaned,
        re.IGNORECASE,
    )
    if inline_cols_match:
        raw_cols = inline_cols_match.group(1).strip()
        for c in raw_cols.split(","):
            c_clean = c.strip().strip(".-*•\"' ")
            # Strip trailing comments or words
            c_clean = re.split(r"\s+(?:where|group|order|limit|from|and|with|having)\b", c_clean, flags=re.IGNORECASE)[0].strip()
            if c_clean and c_clean not in requested_fields:
                requested_fields.append(c_clean)

    prompt_lower = cleaned.lower()

    # 3. Status Filter Concepts
    status_explicit_match = re.search(
        r"\bstatus\s*(?:=|is|in|:)\s*['\"]?([a-zA-Z0-9_\s,]+)['\"]?",
        prompt_lower,
    )
    if status_explicit_match:
        raw_vals = status_explicit_match.group(1).strip()
        stat_list = [v.strip().strip("'\"") for v in raw_vals.split(",") if v.strip()]
        if stat_list:
            requested_filters.append(
                BusinessFilter(
                    concept="status",
                    operator="IN",
                    value=stat_list,
                    raw_text=status_explicit_match.group(0),
                )
            )
    elif re.search(r"\b(?:only\s+)?unpaid\b", prompt_lower):
        requested_filters.append(
            BusinessFilter(
                concept="status",
                operator="IN",
                value=["unpaid", "pending", "open", "draft", "awaiting"],
                raw_text="unpaid",
            )
        )
    elif re.search(r"\b(?:only\s+)?(?:open|pending)\b", prompt_lower):
        requested_filters.append(
            BusinessFilter(
                concept="status",
                operator="IN",
                value=["open", "pending", "in progress", "active", "submitted", "review"],
                raw_text="open/pending",
            )
        )
    elif re.search(r"\b(?:only\s+)?(?:paid|completed|closed)\b", prompt_lower):
        requested_filters.append(
            BusinessFilter(
                concept="status",
                operator="IN",
                value=["paid", "completed", "closed", "finish"],
                raw_text="paid/completed",
            )
        )
    elif re.search(r"\b(?:only\s+)?unmatched\b", prompt_lower):
        requested_filters.append(
            BusinessFilter(
                concept="status",
                operator="IN",
                value=["unmatched", "partially matched"],
                raw_text="unmatched",
            )
        )
    elif re.search(r"\b(?:only\s+)?matched\b", prompt_lower):
        requested_filters.append(
            BusinessFilter(
                concept="status",
                operator="IN",
                value=["matched"],
                raw_text="matched",
            )
        )

    # 4. Numeric Comparison Filters (e.g. amount > 1000, total >= 500, cost < 200)
    numeric_comparisons = re.finditer(
        r"\b([a-zA-Z0-9_]+)\s*(>=|<=|!=|<>|>|<|=)\s*(\d+(?:\.\d+)?)\b",
        cleaned,
    )
    for num_match in numeric_comparisons:
        field_candidate = num_match.group(1).strip()
        op = num_match.group(2).strip()
        val_str = num_match.group(3).strip()
        val_num = float(val_str) if "." in val_str else int(val_str)
        # Avoid matching common words that look like operators or dates
        if field_candidate.lower() not in ("limit", "top", "version", "step", "rule", "day", "days"):
            requested_filters.append(
                BusinessFilter(
                    concept=field_candidate,
                    operator=op,
                    value=val_num,
                    raw_text=num_match.group(0),
                )
            )

    # 5. Date Range Concepts
    due_next_match = re.search(r"\bdue\s+in\s+(?:the\s+)?next\s+(\d+)\s+days?\b", prompt_lower)
    if due_next_match:
        days = int(due_next_match.group(1))
        requested_filters.append(
            BusinessFilter(
                concept="due_date",
                operator="DUE_NEXT_DAYS",
                value=days,
                raw_text=due_next_match.group(0),
            )
        )
    else:
        last_days_match = re.search(r"\b(?:in\s+the\s+|in\s+last\s+|last\s+|past\s+)(\d+)\s+days?\b", prompt_lower)
        if last_days_match:
            days = int(last_days_match.group(1))
            requested_filters.append(
                BusinessFilter(
                    concept="date_range",
                    operator="LAST_DAYS",
                    value=days,
                    raw_text=last_days_match.group(0),
                )
            )

        next_days_match = re.search(r"\b(?:in\s+the\s+)?next\s+(\d+)\s+days?\b", prompt_lower)
        if next_days_match and not due_next_match:
            days = int(next_days_match.group(1))
            requested_filters.append(
                BusinessFilter(
                    concept="date_range",
                    operator="NEXT_DAYS",
                    value=days,
                    raw_text=next_days_match.group(0),
                )
            )

        older_days_match = re.search(r"\b(?:older|more)\s+than\s+(\d+)\s+days?\b", prompt_lower)
        if older_days_match:
            days = int(older_days_match.group(1))
            requested_filters.append(
                BusinessFilter(
                    concept="date_range",
                    operator="OLDER_THAN_DAYS",
                    value=days,
                    raw_text=older_days_match.group(0),
                )
            )

    # 6. Group By Concepts
    group_matches = re.finditer(
        r"\bgroup(?:ed)?\s+by\s+([a-zA-Z0-9_]+(?:\s+[a-zA-Z0-9_]+)?)", prompt_lower
    )
    for gm in group_matches:
        raw_concept = gm.group(1).strip()
        concept = re.split(r"\b(?:and|or|with|having|order|sort|highest|lowest|first)\b", raw_concept)[0].strip()
        if concept and concept not in group_by_concepts:
            group_by_concepts.append(concept)

    # 7. Sorting Concepts
    highest_match = re.search(r"\bhighest\s+([a-zA-Z0-9_]+(?:\s+[a-zA-Z0-9_]+)?)\s+first\b", prompt_lower)
    if highest_match:
        concept = highest_match.group(1).strip()
        sort_concepts.append(BusinessSort(concept=concept, direction="DESC"))

    lowest_match = re.search(r"\blowest\s+([a-zA-Z0-9_]+(?:\s+[a-zA-Z0-9_]+)?)\s+first\b", prompt_lower)
    if lowest_match:
        concept = lowest_match.group(1).strip()
        sort_concepts.append(BusinessSort(concept=concept, direction="ASC"))

    if re.search(r"\b(?:newest|latest)\s+first\b", prompt_lower):
        sort_concepts.append(BusinessSort(concept="date", direction="DESC"))
    elif re.search(r"\b(?:oldest|earliest)\s+first\b", prompt_lower):
        sort_concepts.append(BusinessSort(concept="date", direction="ASC"))

    order_matches = re.finditer(
        r"\b(?:sort|order)(?:ed)?\s+by\s+(?!asc\b|desc\b)([a-zA-Z0-9_]+(?:\s+(?!asc\b|desc\b|ascending\b|descending\b)[a-zA-Z0-9_]+)?)(?:\s+(asc|desc|ascending|descending))?\b",
        prompt_lower,
    )
    for om in order_matches:
        c_name = om.group(1).strip()
        direction_raw = (om.group(2) or "asc").lower()
        direction = "DESC" if "desc" in direction_raw else "ASC"
        if c_name not in [s.concept for s in sort_concepts]:
            sort_concepts.append(BusinessSort(concept=c_name, direction=direction))

    # 8. Calculations Concepts
    if re.search(r"\bcount\s+(?:of|by)\b|\bcount\b", prompt_lower):
        requested_calculations.append("count")
    if re.search(r"\btotal\s+[a-zA-Z0-9_]+|\bsum\s+of\b", prompt_lower):
        requested_calculations.append("sum")
    if re.search(r"\baverage\s+[a-zA-Z0-9_]+|\bavg\b", prompt_lower):
        requested_calculations.append("avg")
    if re.search(r"\baging\s+bucket|\bbucketed\s+by\s+age\b", prompt_lower):
        requested_calculations.append("aging_bucket")
    if re.search(r"\bdays\s+past\s+due\b", prompt_lower):
        requested_calculations.append("days_past_due")
    if re.search(r"\bturnaround\s+hours\b", prompt_lower):
        requested_calculations.append("turnaround_hours")

    return PromptIntent(
        title=title,
        description=description,
        requested_tables=requested_tables,
        requested_fields=requested_fields,
        requested_filters=requested_filters,
        group_by_concepts=group_by_concepts,
        sort_concepts=sort_concepts,
        requested_calculations=requested_calculations,
        warnings=warnings,
    )

