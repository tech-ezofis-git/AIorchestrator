"""SQL safety and schema validator for Report Agent queries."""
from __future__ import annotations

import logging
import re
from typing import Optional, Sequence
from app.report_agent.metadata_service import DatabaseSchema

logger = logging.getLogger("orchestrator.report_agent.sql_validator")

# Forbidden SQL tokens and commands (case-insensitive word boundary check)
_FORBIDDEN_KEYWORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "create",
        "replace",
        "grant",
        "revoke",
        "vacuum",
        "reindex",
        "cluster",
        "lock",
        "copy",
        "merge",
        "call",
        "do",
        "execute",
        "exec",
        "set",
        "reset",
        "show",
        "listen",
        "notify",
        "unlisten",
        "discard",
        "load",
        "import",
        "export",
    }
)

# Forbidden dangerous functions / system procedures
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_write_file",
        "pg_write_binary_file",
        "dblink",
        "dblink_exec",
        "lo_import",
        "lo_export",
        "lo_unlink",
        "query_to_xml",
        "table_to_xml",
        "schema_to_xml",
        "database_to_xml",
        "pg_sleep",
        "xp_cmdshell",
    }
)

_SEMICOLON_MULTI_STMT_PATTERN = re.compile(r";\s*\S+")


class SqlValidationError(Exception):
    """Raised when generated SQL fails safety or schema validation."""

    def __init__(self, message: str, errors: Optional[list[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


def validate_read_only_sql(
    sql: str,
    schema: Optional[DatabaseSchema] = None,
    allowed_tables: Optional[Sequence[str]] = None,
    max_limit: int = 500,
) -> tuple[bool, list[str]]:
    """Validate that the generated SQL is strictly read-only, safe, and conforms to schema.

    Returns:
        tuple[bool, list[str]]: (is_valid, list_of_error_messages)
    """
    errors: list[str] = []
    cleaned_sql = sql.strip()

    if not cleaned_sql:
        errors.append("SQL query cannot be empty.")
        return False, errors

    # Check 1: Must begin with SELECT or WITH
    first_word_match = re.match(r"^\s*(\w+)", cleaned_sql, re.IGNORECASE)
    if not first_word_match:
        errors.append("Invalid SQL syntax: missing leading command.")
        return False, errors

    first_word = first_word_match.group(1).lower()
    if first_word not in ("select", "with"):
        errors.append(
            f"Only read-only SELECT or WITH queries are permitted. Found statement starting with '{first_word.upper()}'."
        )

    # Check 2: Prevent multiple statements / statement chaining
    # Remove strings literal quotes first before checking for semicolons
    sql_without_strings = re.sub(r"'(''|[^'])*'", "''", cleaned_sql)
    sql_without_comments = re.sub(r"--[^\n]*", "", sql_without_strings)
    sql_without_comments = re.sub(r"/\*[\s\S]*?\*/", "", sql_without_comments)

    if _SEMICOLON_MULTI_STMT_PATTERN.search(sql_without_comments):
        errors.append("Multiple statements separated by semicolons are strictly prohibited.")

    # Check 3: Search for forbidden DML/DDL mutation keywords as standalone words
    tokens = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", sql_without_comments.lower())
    for token in tokens:
        if token in _FORBIDDEN_KEYWORDS:
            errors.append(f"Forbidden SQL mutation keyword detected: '{token.upper()}'.")
        if token in _FORBIDDEN_FUNCTIONS:
            errors.append(f"Forbidden system function detected: '{token}'.")

    # Check 4: System catalog table access restriction
    for forbidden_schema in ("pg_catalog.", "information_schema."):
        if forbidden_schema in sql_without_strings.lower():
            # Allow information_schema only if explicit metadata query, but report queries shouldn't target it
            errors.append(f"Direct querying of system catalog '{forbidden_schema[:-1]}' is prohibited in report queries.")

    # Check 5: Schema and table validation if schema is provided
    if schema is not None and schema.tables:
        known_tables = {t[1].lower() for t in schema.tables}
        known_qualified_tables = {f"{t[0].lower()}.{t[1].lower()}" for t in schema.tables}
        
        # Simple extraction of FROM and JOIN table references
        table_matches = re.findall(
            r"\b(?:from|join)\s+([a-zA-Z0-9_\.\"]+)",
            sql_without_comments,
            re.IGNORECASE,
        )
        for tbl_ref in table_matches:
            tbl_clean = tbl_ref.replace('"', '').strip().lower()
            # Ignore subqueries or common aliases/functions if any
            if tbl_clean in ("(", "unnest", "generate_series", "json_array_elements"):
                continue
            
            # Check if table exists in schema
            table_found = (
                tbl_clean in known_tables
                or tbl_clean in known_qualified_tables
                or (allowed_tables and any(tbl_clean == a.replace('"', '').strip().lower() for a in allowed_tables))
            )
            if not table_found and "." in tbl_clean:
                # Check basename part
                base_name = tbl_clean.split(".")[-1]
                table_found = base_name in known_tables

            if not table_found and schema.tables:
                errors.append(f"Referenced table '{tbl_ref}' does not exist in the verified database schema.")

    # Check 6: Limit validation
    limit_match = re.search(r"\blimit\s+(\d+)", sql_without_comments, re.IGNORECASE)
    if limit_match:
        limit_val = int(limit_match.group(1))
        if limit_val > max_limit:
            errors.append(f"Query LIMIT ({limit_val}) exceeds maximum permitted limit ({max_limit}).")
        elif limit_val <= 0:
            errors.append(f"Query LIMIT must be positive. Found: {limit_val}.")

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning("sql_validation_failed", extra={"errors": errors, "sql_snippet": cleaned_sql[:150]})
    return is_valid, errors
