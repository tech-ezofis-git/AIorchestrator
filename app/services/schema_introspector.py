"""Dynamic Schema Introspection Engine for Ezofis Forms.

Queries wForm, wFormControl, and PostgreSQL information_schema.columns
to provide comprehensive schema context to the AI Form Sync Agent.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("orchestrator.schema_introspector")

# In-memory schema cache: key -> (timestamp, schema_dict)
_SCHEMA_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour


def normalize_form_id(form_id: str) -> str:
    """Extract clean form identifier, stripping whitespace or prefixes."""
    return str(form_id).strip().lower()


def get_table_prefix(form_id: str) -> str:
    """Extract the 8-character table prefix used in ezfb_{prefix}_items."""
    clean = normalize_form_id(form_id).replace("-", "")
    return clean[:8]


def introspect_form_schema(conn, form_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch metadata for a given form from PostgreSQL wForm, wFormControl,
    and physical table information_schema.
    """
    norm_id = normalize_form_id(form_id)
    cache_key = f"schema_{norm_id}"

    now = time.time()
    if not force_refresh and cache_key in _SCHEMA_CACHE:
        cached_time, cached_schema = _SCHEMA_CACHE[cache_key]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_schema

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Query dbo."wForm"
        cur.execute(
            """
            SELECT id::text as id, name, type, description, "tenantId"::text as tenant_id
            FROM dbo."wForm"
            WHERE id::text = %s OR id::text LIKE %s
            LIMIT 1;
            """,
            (norm_id, f"{norm_id}%")
        )
        form_row = cur.fetchone()
        if not form_row:
            # Fallback if form not registered in wForm table but physical table exists
            table_prefix = get_table_prefix(norm_id)
            full_form_id = norm_id
            form_name = f"Form_{table_prefix}"
            form_type = "UNKNOWN"
            tenant_id = None
        else:
            full_form_id = form_row["id"]
            table_prefix = get_table_prefix(full_form_id)
            form_name = form_row["name"] or f"Form_{table_prefix}"
            form_type = form_row["type"] or "WORKFLOW"
            tenant_id = form_row["tenant_id"]

        # 2. Query dbo."wFormControl"
        controls: List[Dict[str, Any]] = []
        if form_row:
            cur.execute(
                """
                SELECT id::text as id, name, "columnName" as column_name, type,
                       "isMandatory" as is_mandatory, "validationJson" as validation_json
                FROM dbo."wFormControl"
                WHERE "wFormId"::text = %s AND "isDeleted" = false
                ORDER BY "createdAt" ASC;
                """,
                (full_form_id,)
            )
            for row in cur.fetchall():
                controls.append({
                    "id": row["id"],
                    "name": row["name"],
                    "column_name": row["column_name"],
                    "type": row["type"],
                    "is_mandatory": bool(row["is_mandatory"]),
                })

        # 3. Query physical table columns from information_schema
        table_name = f"ezfb_{table_prefix}_items"
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'dbo' AND table_name = %s
            ORDER BY ordinal_position ASC;
            """,
            (table_name,)
        )
        col_rows = cur.fetchall()
        physical_columns: Dict[str, Dict[str, Any]] = {}
        for c in col_rows:
            physical_columns[c["column_name"]] = {
                "data_type": c["data_type"],
                "is_nullable": c["is_nullable"] == "YES"
            }

    schema_data = {
        "form_id": full_form_id,
        "table_prefix": table_prefix,
        "table_name": table_name,
        "table_exists": len(physical_columns) > 0,
        "form_name": form_name,
        "form_type": form_type,
        "tenant_id": tenant_id,
        "controls": controls,
        "physical_columns": physical_columns,
    }

    _SCHEMA_CACHE[cache_key] = (now, schema_data)
    return schema_data


def format_schema_for_llm(schema: Dict[str, Any], include_system_cols: bool = False) -> str:
    """Format the schema into a concise, token-efficient markdown representation
    tailored for LLM reasoning and mapping.
    """
    system_cols = {
        "item_id", "id", "created_at", "modified_at", "created_by",
        "modified_by", "is_deleted", "today_task", "is_marked",
        "tenant_id"
    }

    lines = [
        f"Form Name: {schema.get('form_name')} (Type: {schema.get('form_type')})",
        f"Table: dbo.{schema.get('table_name')}",
        "Available Columns:"
    ]

    physical_cols = schema.get("physical_columns", {})
    controls = schema.get("controls", [])

    control_map = {}
    for ctrl in controls:
        if ctrl.get("column_name"):
            control_map[ctrl["column_name"]] = ctrl
        if ctrl.get("name"):
            control_map[ctrl["name"]] = ctrl

    if physical_cols:
        for col_name, col_meta in physical_cols.items():
            if not include_system_cols and col_name.lower() in system_cols:
                continue
            ctrl = control_map.get(col_name)
            label = ctrl["name"] if ctrl else col_name
            ctrl_type = ctrl["type"] if ctrl else col_meta.get("data_type", "text")
            lines.append(f"  - Column: `{col_name}` | Label: \"{label}\" | Type: {ctrl_type}")
    else:
        for ctrl in controls:
            col_name = ctrl.get("column_name") or ctrl.get("name")
            label = ctrl.get("name")
            lines.append(f"  - Column: `{col_name}` | Label: \"{label}\" | Type: {ctrl.get('type')}")

    return "\n".join(lines)
