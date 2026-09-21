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

_INTERNAL_WORKFLOW_PLUMBING_PREFIXES = (
    "workflow_tasks_",
    "workflow_step_instances_",
    "workflow_comments_",
    "workflow_attachments_",
    "workflow_signatures_",
    "workflow_notifications_",
    "workflow_activity_",
    "workflow_audit_",
    "workflow_history_",
    "workflow_instance_slas_",
    "workflow_instance_user_state_",
    "workflow_ai_validations_",
    "agent_data_validation_",
    "workflow_pdf_annotations_",
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

def _clean_str(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", text.lower())


_DOMAIN_SEMANTIC_TOKENS: dict[str, list[str]] = {
    "accounts payable": [
        "invoice",
        "invoices",
        "bill",
        "bills",
        "po",
        "ponumber",
        "vendor",
        "supplier",
        "due",
        "duedate",
        "payment",
        "amount",
        "matched",
        "match",
    ],
    "workflow automation": [
        "instance",
        "instances",
        "step",
        "sla",
        "stage",
        "task",
        "request",
        "transition",
        "status",
        "pending",
    ],
    "document management": [
        "repository",
        "item",
        "filename",
        "document",
        "retention",
        "version",
        "access",
        "deletion",
        "archive",
    ],
    "external portal": [
        "portal",
        "submission",
        "submissions",
        "form",
        "ezfb",
        "response",
        "feedback",
    ],
    "user sessions & security": [
        "user",
        "session",
        "login",
        "auth",
        "security",
        "audit",
        "client_ip",
        "event",
    ],
    "report agent impact & roi": [
        "credit",
        "ledger",
        "run",
        "usage",
        "cost",
        "tokens",
        "savings",
        "value",
    ],
}


def _extract_intent_tokens(template: TemplateDefinition) -> list[str]:
    """Extract meaningful search tokens from template intent and domain semantics."""
    combined = f"{template.title} {template.domain} {template.description}"
    words = re.findall(r"[a-zA-Z0-9]+", combined.lower())
    tokens: list[str] = []
    seen = set()

    for w in words:
        if len(w) >= 2 and w not in _STOP_WORDS and w not in seen:
            tokens.append(w)
            seen.add(w)

            # Dynamically derive root / singular forms (e.g., 'invoices' -> 'invoice', 'requests' -> 'request')
            if len(w) > 4:
                if w.endswith("ies") and len(w) > 4:
                    base = w[:-3] + "y"
                    if base not in seen:
                        tokens.append(base)
                        seen.add(base)
                elif w.endswith("es") and len(w) > 4:
                    base = w[:-2]
                    if base not in seen:
                        tokens.append(base)
                        seen.add(base)
                elif w.endswith("s") and not w.endswith("ss"):
                    base = w[:-1]
                    if base not in seen:
                        tokens.append(base)
                        seen.add(base)

    # Enrich with domain semantic tokens
    for token in _DOMAIN_SEMANTIC_TOKENS.get(template.domain.lower(), []):
        if token not in seen:
            tokens.append(token)
            seen.add(token)

    return tokens


def _score_table(
    schema: str,
    table: str,
    columns: list[ColumnMeta],
    template: TemplateDefinition,
    intent_tokens: list[str],
    db_schema: Optional[DatabaseSchema] = None,
) -> float:
    table_lower = table.lower()
    schema_lower = schema.lower()
    is_roi_domain = template.domain == "Report Agent Impact & ROI"
    is_ap_domain = "accounts payable" in template.domain.lower() or "accounts payable" in template.title.lower()

    # 1. System orchestrator table isolation
    is_system_table = table_lower in _SYSTEM_ORCHESTRATOR_TABLES or table_lower.startswith("ap_") or table_lower.startswith("catalog_")
    if is_system_table:
        if not is_roi_domain:
            return -1000.0
        else:
            return 80.0

    score = 0.0

    # 1a. Internal workflow engine plumbing tables (tasks, steps, comments, audits)
    is_internal_plumbing = any(table_lower.startswith(p) for p in _INTERNAL_WORKFLOW_PLUMBING_PREFIXES)
    if is_internal_plumbing:
        score -= 200.0

    # 1b. Workflow metadata check (from wworkflow definitions)
    if db_schema and db_schema.table_to_workflow:
        wf_name = db_schema.get_workflow_for_table(table)
        if wf_name:
            wf_clean = _clean_str(wf_name)
            domain_clean = _clean_str(template.domain)
            title_clean = _clean_str(template.title)
            if domain_clean in wf_clean or wf_clean in domain_clean or title_clean in wf_clean or wf_clean in title_clean:
                score += 80.0
            else:
                # Table belongs to an explicitly different workflow (e.g. Vessel, HR, Logistics)
                score -= 300.0

    # 2. Match intent tokens against table name
    table_clean = _clean_str(table_lower)
    for token in intent_tokens:
        token_clean = _clean_str(token)
        if len(token_clean) >= 3 and token_clean in table_clean:
            score += 15.0

    # Domain-specific table structure priority
    if is_ap_domain or "external portal" in template.domain.lower() or "form" in template.title.lower():
        if table_lower.startswith("ezfb_") or table_lower.startswith("process_form_"):
            score += 60.0
        elif table_lower.startswith("items_"):
            score += 15.0
    elif "workflow" in template.domain.lower():
        if table_lower.startswith("workflow_instances") or table_lower in ("workflow_instances", "workflowinstances"):
            score += 60.0
    elif "document" in template.domain.lower():
        if table_lower.startswith("items_") or table_lower in ("repositoryitem", "repository_item"):
            score += 60.0

    # 3. Match columns against intent tokens
    matching_cols = 0
    has_date_col = False
    has_numeric_col = False
    has_vessel_col = False
    has_ap_invoice_col = False
    has_po_col = False

    for col in columns:
        col_clean = _clean_str(col.column)
        if col_clean in _SKIP_COLUMNS:
            continue

        if any(k in col_clean for k in ("vessel", "imo", "voyage", "portofentry", "berth", "cargo", "etd", "eta")):
            has_vessel_col = True

        if any(k in col_clean for k in ("invoice", "invoiceno", "invoicenum", "invoicenumber", "invoiceamount", "invoicedate", "bill", "billno", "billamount", "billnumber", "matchedstatus")):
            has_ap_invoice_col = True

        if any(k in col_clean for k in ("ponumber", "poamount", "podate", "supplier")):
            has_po_col = True

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

    # Penalize non-AP tables that have distinct non-AP columns (e.g. Vessel tables) when requesting AP reports
    if is_ap_domain:
        if has_vessel_col:
            score -= 200.0
        if has_ap_invoice_col:
            # Major boost for actual Accounts Payable invoice tables
            score += 80.0
        elif has_po_col and not has_ap_invoice_col:
            # Pure purchase order / supplier master tables are not Accounts Payable invoice aging tables
            score -= 60.0
        if not has_numeric_col:
            # Aging reports strictly require financial amount metrics
            score -= 100.0

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

    # 6. Penalize staging, temporary, history, and backup tables heavily
    if any(table_lower.endswith(sfx) for sfx in ("_stage", "_staging", "_history", "_backup", "_bak", "_temp", "_tmp", "_draft", "_audit")):
        score -= 250.0
    elif any(tag in table_lower for tag in ("_stage_", "_staging_", "_history_", "_temp_")):
        score -= 250.0

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
    max_tables: int = 1,
    max_fields_per_table: int = 15,
) -> tuple[list[str], list[DiscoveredField], list[MissingField]]:
    """Discover relevant tables and fields directly from the database schema based on template intent."""
    intent_tokens = _extract_intent_tokens(template)
    scored_tables: list[tuple[float, str, str, list[ColumnMeta]]] = []

    for (s, t), cols in schema.columns_by_table.items():
        score = _score_table(s, t, cols, template, intent_tokens, db_schema=schema)
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
