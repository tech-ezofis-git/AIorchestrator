"""SQL generator service — converts structured ReportPlan into safe read-only PostgreSQL query."""
from __future__ import annotations

import logging
import re
from typing import Any
from app.models.report_agent import ReportFilter, ReportPlan

logger = logging.getLogger("orchestrator.report_agent.sql_generator")


def _quote_ident(ident: str) -> str:
    """Safely quote a SQL identifier, preserving qualified parts."""
    clean = ident.strip(' "')
    if "." in clean:
        parts = [p.strip(' "') for p in clean.split(".") if p.strip(' "')]
        return ".".join(f'"{p}"' for p in parts)
    return f'"{clean}"'


def _format_literal(val: Any) -> str:
    """Format Python value as safe SQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    # Escape single quotes in strings
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def _format_filter(filt: ReportFilter) -> str:
    """Format a ReportFilter into SQL WHERE condition."""
    col = _quote_ident(filt.field)
    op = (filt.operator or "=").strip().upper()

    if op in ("IS NULL", "IS NOT NULL"):
        return f"{col} {op}"

    val = filt.value

    # Date range relative operators
    if op == "DUE_NEXT_DAYS":
        days = int(val) if isinstance(val, (int, str)) and str(val).isdigit() else 30
        return f"(CAST({col} AS DATE) >= CURRENT_DATE AND CAST({col} AS DATE) <= CURRENT_DATE + INTERVAL '{days} days')"

    if op == "LAST_DAYS":
        days = int(val) if isinstance(val, (int, str)) and str(val).isdigit() else 30
        return f"(CAST({col} AS DATE) >= CURRENT_DATE - INTERVAL '{days} days')"

    if op == "NEXT_DAYS":
        days = int(val) if isinstance(val, (int, str)) and str(val).isdigit() else 30
        return f"(CAST({col} AS DATE) >= CURRENT_DATE AND CAST({col} AS DATE) <= CURRENT_DATE + INTERVAL '{days} days')"

    if op == "OLDER_THAN_DAYS":
        days = int(val) if isinstance(val, (int, str)) and str(val).isdigit() else 30
        return f"(CAST({col} AS DATE) <= CURRENT_DATE - INTERVAL '{days} days')"

    if op == "BETWEEN" and isinstance(val, (list, tuple)) and len(val) == 2:
        return f"({col} BETWEEN {_format_literal(val[0])} AND {_format_literal(val[1])})"

    if op in ("IN", "NOT IN"):
        if isinstance(val, (list, tuple, set)):
            items = [_format_literal(v) for v in val if v is not None]
            if not items:
                return f"{col} IS NOT NULL"
            return f"{col} {op} ({', '.join(items)})"
        return f"{col} {op} ({_format_literal(val)})"

    if op in ("LIKE", "ILIKE"):
        return f"{col} {op} {_format_literal(val)}"

    if op in ("=", "!=", "<>", ">", "<", ">=", "<="):
        return f"{col} {op} {_format_literal(val)}"

    # Default equality
    return f"{col} = {_format_literal(val)}"


def generate_sql(report_plan: ReportPlan, limit: int = 50) -> str:
    """Generate clean, formatted, read-only SQL from ReportPlan."""
    # 1. SELECT clause
    select_parts: list[str] = []

    for col in report_plan.columns:
        select_parts.append(_quote_ident(col.field))

    for calc in report_plan.calculations:
        # Wrap calculation expression safely
        select_parts.append(f"{calc.expression} AS {_quote_ident(calc.label)}")

    if not select_parts:
        select_parts.append("*")

    # 2. FROM clause
    source = report_plan.source
    if source.schema_name and source.schema_name.lower() not in ("", "none"):
        table_ref = f'"{source.schema_name}"."{source.table}"'
    elif "." in source.table:
        parts = [p.strip(' "') for p in source.table.split(".") if p.strip(' "')]
        table_ref = ".".join(f'"{p}"' for p in parts)
    else:
        table_ref = f'"{source.table}"'

    sql_lines = [
        "SELECT",
        "    " + ",\n    ".join(select_parts),
        f"FROM {table_ref}",
    ]

    # Joins if any
    for join in source.joins:
        j_type = join.get("type", "LEFT JOIN")
        j_table = join.get("table", "")
        j_on = join.get("on", "")
        if j_table and j_on:
            sql_lines.append(f"{j_type} {j_table} ON {j_on}")

    # 3. WHERE clause
    if report_plan.filters:
        filter_exprs = [_format_filter(f) for f in report_plan.filters]
        sql_lines.append("WHERE " + " AND ".join(filter_exprs))

    # 4. GROUP BY clause
    if report_plan.group_by:
        group_cols = [_quote_ident(g) for g in report_plan.group_by]
        sql_lines.append("GROUP BY " + ", ".join(group_cols))

    # 5. ORDER BY clause
    if report_plan.order_by:
        order_parts = []
        for s in report_plan.order_by:
            dir_str = "DESC" if (s.direction or "").upper() == "DESC" else "ASC"
            order_parts.append(f"{_quote_ident(s.field)} {dir_str}")
        sql_lines.append("ORDER BY " + ", ".join(order_parts))

    # 6. LIMIT clause
    safe_limit = max(1, min(int(limit), 500))
    sql_lines.append(f"LIMIT {safe_limit};")

    return "\n".join(sql_lines)
