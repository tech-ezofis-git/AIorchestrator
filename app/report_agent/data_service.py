"""Data service for sampling table data and executing validated report queries."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any, Optional
import uuid

from app.models.report_agent import ReportData

logger = logging.getLogger("orchestrator.report_agent.data_service")


def _sanitize_cell_value(val: Any) -> Any:
    """Convert asyncpg / database types to JSON-serializable primitives."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, Decimal):
        return float(val) if val % 1 else int(val)
    if isinstance(val, (bytes, bytearray)):
        return f"<binary {len(val)} bytes>"
    if isinstance(val, (list, tuple)):
        return [_sanitize_cell_value(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _sanitize_cell_value(v) for k, v in val.items()}
    return val


def _format_row(row: Any) -> dict[str, Any]:
    """Convert asyncpg Record or mapping to a standard dict."""
    if hasattr(row, "keys"):
        return {k: _sanitize_cell_value(row[k]) for k in row.keys()}
    if isinstance(row, dict):
        return {k: _sanitize_cell_value(v) for k, v in row.items()}
    return {"val": _sanitize_cell_value(row)}


async def sample_table_values(
    db: Any,
    table_identifier: str,
    columns: list[str],
    limit: int = 20,
) -> dict[str, Any]:
    """Sample actual values from a table to understand data distributions.

    Returns:
        dict with:
        - "samples": list of sample rows
        - "distinct_values": dict of {col_name: [val1, val2, ...]} for categorical columns
    """
    result: dict[str, Any] = {"samples": [], "distinct_values": {}}
    if not table_identifier or not columns:
        return result

    # Sanitize table identifier (schema.table or table)
    parts = [p.strip(' "') for p in table_identifier.split(".") if p.strip(' "')]
    safe_table = ".".join(f'"{p}"' for p in parts)

    # 1. Fetch sample rows
    safe_cols = ", ".join(f'"{c}"' for c in columns[:15])
    sample_sql = f"SELECT {safe_cols} FROM {safe_table} LIMIT {max(1, min(limit, 50))};"
    try:
        rows = await db.fetch(sample_sql)
        for r in rows or []:
            result["samples"].append(_format_row(r))
    except Exception as exc:
        logger.warning(
            "sample_table_fetch_failed",
            extra={"table": table_identifier, "error": str(exc)[:200]},
        )

    # 2. For likely categorical columns (status, stage, state, type), get distinct values
    cat_keywords = ("status", "state", "stage", "type", "category", "phase", "priority")
    for col in columns:
        col_lower = col.lower()
        if any(kw in col_lower for kw in cat_keywords):
            dist_sql = f'SELECT DISTINCT "{col}" AS val FROM {safe_table} WHERE "{col}" IS NOT NULL LIMIT 15;'
            try:
                dist_rows = await db.fetch(dist_sql)
                vals = [str(_sanitize_cell_value(r.get("val") if isinstance(r, dict) else r["val"])) for r in dist_rows or [] if r]
                vals = [v for v in vals if v and v != "None"]
                if vals:
                    result["distinct_values"][col] = vals
            except Exception:
                pass

    return result


async def verify_table_accessible(db: Any, table_identifier: str) -> bool:
    """Verify that a table actually exists and is readable in the current connection."""
    if not table_identifier or db is None:
        return True
    parts = [p.strip(' "') for p in table_identifier.split(".") if p.strip(' "')]
    safe_table = ".".join(f'"{p}"' for p in parts)
    try:
        await db.fetch(f"SELECT 1 FROM {safe_table} LIMIT 1;")
        return True
    except Exception as exc:
        logger.warning(
            "table_accessibility_check_failed",
            extra={"table": table_identifier, "error": str(exc)[:200]},
        )
        return False


async def execute_report_query(
    db: Any,
    sql: str,
    timeout_sec: float = 15.0,
) -> ReportData:
    """Execute a validated read-only SQL query and return structured ReportData."""
    t0 = asyncio.get_event_loop().time()
    try:
        # Run query with asyncio timeout guard
        rows = await asyncio.wait_for(db.fetch(sql), timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        logger.error("report_query_timeout", extra={"sql": sql[:200], "timeout": timeout_sec})
        raise TimeoutError(f"Database query timed out after {timeout_sec} seconds.") from exc
    except Exception as exc:
        logger.error(
            "report_query_execution_failed",
            extra={"sql": sql[:200], "error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        raise RuntimeError(f"Database query execution failed: {str(exc)}") from exc

    duration_ms = round((asyncio.get_event_loop().time() - t0) * 1000, 2)
    formatted_rows: list[dict[str, Any]] = []
    columns_list: list[str] = []

    if rows:
        first_row = rows[0]
        if hasattr(first_row, "keys"):
            columns_list = list(first_row.keys())
        elif isinstance(first_row, dict):
            columns_list = list(first_row.keys())
        
        for r in rows:
            formatted_rows.append(_format_row(r))

    logger.info(
        "report_query_executed",
        extra={
            "row_count": len(formatted_rows),
            "columns_count": len(columns_list),
            "duration_ms": duration_ms,
        },
    )

    return ReportData(
        row_count=len(formatted_rows),
        columns=columns_list,
        rows=formatted_rows,
    )
