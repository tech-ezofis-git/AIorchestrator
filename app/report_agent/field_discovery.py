"""Dynamic database field and table discovery based on template business intent and real schema."""
from __future__ import annotations

import re
from typing import Optional
from app.models.report_agent import DiscoveredField, MissingField
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.templates import TemplateDefinition

_SYSTEM_ORCHESTRATOR_TABLES = frozenset(
    {
        "ap_runs",
        "ap_credit_ledger",
        "ap_skill_artifacts",
        "ap_tenant_plans",
        "audit_log",
        "alembic_version",
        "catalog_models",
        "catalog_agents",
        "catalog_tenant_agent_models",
        "chunks",
        "memories",
    }
)

_SKIP_COLUMNS = frozenset(
    {
        "password",
        "password_hash",
        "passwordhash",
        "token",
        "access_token",
        "secret",
        "client_secret",
        "salt",
        "embedding",
        "api_key",
        "apikey",
    }
)

_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "all",
        "any",
        "are",
        "was",
        "were",
        "per",
        "vs",
        "not",
        "has",
        "have",
        "had",
        "by",
        "out",
    }
)

_DOMAIN_SEMANTIC_TOKENS: dict[str, tuple[str, ...]] = {
    "Accounts Payable": ("account", "accounts", "payable", "invoice", "invoices", "bill", "bills", "vendor", "due", "payment", "amount", "balance", "po", "voucher", "item", "items"),
    "Workflow Automation": ("workflow", "process", "instance", "instances", "request", "requests", "stage", "step", "status", "task", "sla", "duration", "history"),
    "Document Management": ("document", "documents", "file", "files", "repository", "item", "items", "version", "retention", "access", "view", "archive", "library"),
    "External Portal": ("portal", "submission", "submissions", "form", "forms", "submit", "request", "entry", "item", "items"),
    "User Sessions & Security": ("user", "users", "login", "session", "sessions", "auth", "security", "audit", "log", "ip", "device"),
    "Report Agent Impact & ROI": ("credit", "credits", "consumption", "roi", "savings", "cost", "value", "run", "runs", "usage", "ledger", "plans"),
}


def _clean_str(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", text.lower())


def _extract_intent_tokens(template: TemplateDefinition) -> list[str]:
    """Extract meaningful search tokens from the template's business metadata and domain vocabulary."""
    combined = f"{template.title} {template.domain} {template.description}"
    words = re.findall(r"[a-zA-Z0-9]+", combined.lower())
    tokens: list[str] = []
    seen = set()
    for w in words:
        if len(w) >= 2 and w not in _STOP_WORDS and w not in seen:
            tokens.append(w)
            seen.add(w)

    # Supplement with domain vocabulary
    for d_name, d_tokens in _DOMAIN_SEMANTIC_TOKENS.items():
        if d_name.lower() in template.domain.lower() or template.domain.lower() in d_name.lower():
            for dt in d_tokens:
                if dt not in seen:
                    tokens.append(dt)
                    seen.add(dt)

    return tokens


def _score_table(
    schema: str,
    table: str,
    columns: list[ColumnMeta],
    template: TemplateDefinition,
    intent_tokens: list[str],
) -> float:
    table_lower = table.lower()
    schema_lower = schema.lower()
    is_roi_domain = template.domain == "Report Agent Impact & ROI"

    # 1. System orchestrator table isolation
    is_system_table = table_lower in _SYSTEM_ORCHESTRATOR_TABLES or table_lower.startswith("ap_") or table_lower.startswith("catalog_")
    if is_system_table:
        if not is_roi_domain:
            return -1000.0
        else:
            return 80.0

    score = 0.0

    # 2. Match intent tokens against table name
    table_clean = _clean_str(table_lower)
    for token in intent_tokens:
        token_clean = _clean_str(token)
        if len(token_clean) >= 3 and token_clean in table_clean:
            score += 15.0

    # 3. Match columns against intent tokens
    matching_cols = 0
    has_date_col = False
    has_numeric_col = False

    for col in columns:
        col_clean = _clean_str(col.column)
        if col_clean in _SKIP_COLUMNS:
            continue

        for token in intent_tokens:
            token_clean = _clean_str(token)
            if len(token_clean) >= 3 and (token_clean in col_clean or col_clean in token_clean):
                score += 8.0
                matching_cols += 1
                break

        # Detect structural utility from column types and names
        col_type_lower = col.data_type.lower()
        if "date" in col_type_lower or "time" in col_type_lower or col_clean.endswith("date") or "date" in col_clean or "at" in col_clean:
            has_date_col = True
        if "int" in col_type_lower or "float" in col_type_lower or "numeric" in col_type_lower or "double" in col_type_lower or any(k in col_clean for k in ("amount", "total", "price", "cost", "balance", "count", "value", "credit")):
            has_numeric_col = True

    # 4. Reward tables that provide both dates and metrics for analytical reports
    if has_date_col:
        score += 10.0
    if has_numeric_col:
        score += 10.0
    if has_date_col and has_numeric_col:
        score += 15.0

    # Boost tables with an actual Due Date for Aging templates
    has_due_date = any("due" in _clean_str(col.column) for col in columns)
    if "aging" in template.title.lower() or "aging" in template.description.lower():
        if has_due_date:
            score += 30.0

    # 5. Schema domain alignment
    if matching_cols > 0 or score > 0:
        if schema_lower == "workflow" and "workflow" in template.domain.lower():
            score += 20.0
        elif schema_lower in ("dbo", "repository", "public"):
            score += 10.0

    # 6. Prefer primary data tables over history / staging tables
    if table_lower.endswith("_history"):
        score -= 5.0
    elif table_lower.endswith("_stage"):
        score -= 3.0

    return score


def _score_column(col_name: str, col_type: str, intent_tokens: list[str]) -> float:
    col_clean = _clean_str(col_name)
    if col_clean in _SKIP_COLUMNS:
        return -100.0

    score = 0.0

    # 1. Match against intent tokens
    for token in intent_tokens:
        token_clean = _clean_str(token)
        if len(token_clean) >= 3 and (token_clean in col_clean or col_clean in token_clean):
            score += 20.0
            if col_clean == token_clean:
                score += 10.0

    # 2. Give bonus to dates, identifiers, and numeric metrics
    t = col_type.lower()
    if "date" in t or "time" in t or col_clean.endswith("date") or "date" in col_clean:
        score += 12.0
    elif "int" in t or "float" in t or "numeric" in t or "double" in t or any(k in col_clean for k in ("amount", "total", "price", "cost", "balance", "credit", "rate")):
        score += 12.0
    elif any(k in col_clean for k in ("id", "number", "no", "name", "title", "code", "status", "stage", "state")):
        score += 8.0

    return score


def find_relevant_fields(
    template: TemplateDefinition,
    schema: DatabaseSchema,
    max_tables: int = 5,
    max_fields_per_table: int = 15,
) -> tuple[list[str], list[DiscoveredField], list[MissingField]]:
    """Discover relevant tables and fields directly from the database schema based on template intent."""
    intent_tokens = _extract_intent_tokens(template)
    scored_tables: list[tuple[float, str, str, list[ColumnMeta]]] = []

    for (s, t), cols in schema.columns_by_table.items():
        score = _score_table(s, t, cols, template, intent_tokens)
        if score > 0:
            scored_tables.append((score, s, t, cols))

    # If no tables scored positively, fallback to non-system tables
    if not scored_tables and schema.tables:
        for (s, t), cols in schema.columns_by_table.items():
            if t.lower() not in _SYSTEM_ORCHESTRATOR_TABLES:
                scored_tables.append((1.0, s, t, cols))

    # Sort tables by score descending
    scored_tables.sort(key=lambda x: x[0], reverse=True)

    discovered_tables: list[str] = []
    discovered_fields: list[DiscoveredField] = []
    seen_cols: set[tuple[str, str]] = set()

    for score, s, t, cols in scored_tables[:max_tables]:
        table_ident = f"{s}.{t}" if s != "public" else t
        if table_ident not in discovered_tables:
            discovered_tables.append(table_ident)

        # Score and rank columns by relevance to template intent
        ranked_cols: list[tuple[float, ColumnMeta]] = []
        for col in cols:
            col_score = _score_column(col.column, col.data_type, intent_tokens)
            if col_score > 0:
                ranked_cols.append((col_score, col))

        # Sort columns by relevance descending
        ranked_cols.sort(key=lambda x: x[0], reverse=True)

        # Select top most relevant columns directly from table
        selected_cols = [c for _, c in ranked_cols[:max_fields_per_table]]

        # If very few columns matched, fallback to first few table columns for context
        if len(selected_cols) < 5:
            existing_names = {c.column.lower() for c in selected_cols}
            for col in cols[:10]:
                if _clean_str(col.column) not in _SKIP_COLUMNS and col.column.lower() not in existing_names:
                    selected_cols.append(col)
                    if len(selected_cols) >= 10:
                        break

        for col in selected_cols:
            col_key = (table_ident, col.column)
            if col_key not in seen_cols:
                seen_cols.add(col_key)
                discovered_fields.append(
                    DiscoveredField(
                        table=table_ident,
                        column=col.column,
                        type=col.data_type,
                    )
                )

    # Dynamic missing field check based on report domain requirements
    missing_fields: list[MissingField] = []
    all_col_names_clean = [_clean_str(f.column) for f in discovered_fields]
    all_col_types_lower = [f.type.lower() for f in discovered_fields]

    # Check for date requirement if title or description implies aging, timeframe, or timeline
    if any(k in template.title.lower() or k in template.description.lower() for k in ("aging", "period", "recent", "time", "date", "compliance", "sla")):
        has_date = any(
            "date" in t
            or "time" in t
            or "date" in c
            or c.endswith("at")
            or any(k in c for k in ("duedt", "timestamp", "createdat", "modifiedat", "submittedat", "startedat", "completedat", "eventtime"))
            for c, t in zip(all_col_names_clean, all_col_types_lower)
            if c not in ("status", "state", "rate", "format", "category")
        )
        if not has_date:
            missing_fields.append(
                MissingField(
                    field="date or timestamp field",
                    status="missing",
                    reason="No suitable date or timestamp column was found in the discovered database schema.",
                )
            )

    return discovered_tables, discovered_fields, missing_fields
