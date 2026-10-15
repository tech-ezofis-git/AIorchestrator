"""Suggest a dashboard message from a tenant repository or workflow."""
from __future__ import annotations

import logging
from typing import Any

from app.dashboard.agent import _sample_columns, _sample_payload, _workflow_fields_for_response
from app.dashboard.llm import LLMError, chat_text
from app.dashboard.propose import _occupancy
from app.dashboard.store import DashboardStore

logger = logging.getLogger("v6_dashboard.prompts")

_SYSTEM_SKIP = {
    "id",
    "tenantid",
    "repositoryid",
    "folderid",
    "storageproviderid",
    "filepath",
    "filename",
    "filesize",
    "totalpages",
    "isverified",
    "ocrscore",
    "aistatus",
    "ocrtext",
    "ocrjson",
    "summaryjson",
    "workflowinstanceid",
    "activeitem",
    "createdatutc",
    "modifiedatutc",
    "createdby",
    "modifiedby",
    "isdeleted",
    "fileversion",
}


_SYSTEM = """You write a professional dashboard specification a manager can read.

The table metadata below is PRIVATE. Use it to decide what the dashboard can show.
Never list technical columns (id, tenant_id, repository_id, folder_id, file_path, ocr_json, created_at_utc, etc.).
Never use awkward labels like "What I need", "Who will use this", "Questions I need answered", or "Numbers I want on the screen".

Write in a calm third-person product brief. Opening line example:
Build an Accounts Payable analytics dashboard so finance can monitor outstanding invoices, overdue amounts, and supplier exposure.

Return markdown only. Do not return JSON. Do not wrap in a code fence.
Hard limit: 80–100 lines. One idea per line.

Use these normal headings only:
# {Repository} Analytics Dashboard
## Business context
## Objectives
## KPI cards
## Charts
## Filters
## Exceptions and follow-up

Under each heading, write short professional bullets (plain English: invoices, suppliers, due dates — not field names).

Rules:
- Only include metrics the business fields can support. Do not invent data.
- Money comes from invoice/amount fields, never file size.
- Aging only if a due date exists. Paid vs outstanding only if match/status exists.
- Different repositories must produce different specs.
- No SQL, APIs, table names, widget JSON, or "use these columns".
"""


def _norm(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _has(occupancy: dict[str, float], *hints: str, min_pct: float = 1.0) -> bool:
    for column, pct in occupancy.items():
        key = _norm(column)
        if any(hint in key for hint in hints) and pct >= min_pct:
            return True
    return False


def _has_column(columns: list[str], *hints: str) -> bool:
    keys = {_norm(name) for name in columns}
    return any(any(hint in key for hint in hints) for key in keys)


def _business_columns(columns: list[str]) -> list[str]:
    return [name for name in columns if name and _norm(name) not in _SYSTEM_SKIP]


def _business_sample(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    keep = set(columns)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slim = {k: v for k, v in row.items() if k in keep}
        if slim:
            out.append(slim)
    return out


def fallback_prompt(
    *,
    target: dict[str, Any],
    columns: list[str],
    occupancy: dict[str, float],
    name_hint: str = "",
) -> str:
    name = (
        (name_hint or "").strip()
        or str(target.get("repository_name") or target.get("workflow_name") or "this repository").strip()
    )
    money = _has_column(columns, "invoiceamount", "invoicetotal") or (
        _has_column(columns, "amount") and not _has_column(columns, "filesize")
    )
    due = _has_column(columns, "duedate")
    match = _has_column(columns, "matchedstatus", "matchstatus")
    supplier = _has_column(columns, "supplier", "vendor")
    department = _has_column(columns, "department")
    filetype = _has_column(columns, "filetype")
    status_empty = not _has(occupancy, "status", "aistatus") if occupancy else False
    match_filled = _has(occupancy, "matchedstatus", "matchstatus") if occupancy else match

    if _has_column(columns, "vessel", "vesselname", "berth", "port"):
        focus = "vessel arrivals, departures, and berth utilization"
    elif _has_column(columns, "rfq", "rfqid", "rfqnumber", "quote"):
        focus = "total RFQs, quote status breakdown, and monthly volume trend"
    elif _has_column(columns, "inventory", "sku", "stock", "warehouse"):
        focus = "stock levels, category distribution, and reorder status"
    elif _has_column(columns, "sales", "opportunity", "deal", "lead"):
        focus = "pipeline value, deal stages, and conversion trend"
    elif money:
        bits = ["total payable"]
        if match:
            bits.append("paid versus outstanding")
        if due:
            bits.append("overdue")
        if supplier:
            bits.append("outstanding by supplier")
        if due:
            bits.append("aging")
        if match or match_filled:
            bits.append("match status")
        elif not status_empty:
            bits.append("invoice status")
        focus = ", ".join(bits)
    elif department or filetype:
        bits = []
        if department:
            bits.append("counts by department")
        if filetype:
            bits.append("file type mix")
        focus = " and ".join(bits) if bits else "record counts"
    else:
        focus = "record counts and the main category breakdowns from this table"

    return "\n".join(
        [
            f"# {name} Analytics Dashboard",
            "",
            "## Business context",
            f"An operations dashboard for {name} focused on {focus}.",
            "",
            "## Objectives",
            "Give managers a clear view of volume, risk, and items that need action.",
            "",
            "## KPI cards",
            "Headline totals, rates, and overdue or exception counts.",
            "",
            "## Charts",
            "Breakdowns and trends that match those KPIs.",
            "",
            "## Filters",
            "Date range, status, and the main business grouping (supplier, department, or type).",
            "",
            "## Exceptions and follow-up",
            "Highlight overdue, unmatched, or incomplete items first.",
        ]
    )


async def generate_prompt(
    *,
    store: DashboardStore,
    tenant_id: str | None,
    repository_id: str | None = None,
    workflow_id: str | None = None,
    repository_name: str = "",
    workflow_name: str = "",
) -> dict[str, Any]:
    target = await store.resolve_target(
        tenant_id=tenant_id,
        repository_id=repository_id,
        workflow_id=workflow_id,
    )
    if repository_name.strip() and not target.get("repository_name"):
        target["repository_name"] = repository_name.strip()
    if workflow_name.strip() and not target.get("workflow_name"):
        target["workflow_name"] = workflow_name.strip()

    columns = await store.list_columns(
        tenant_id=target["tenant_id"],
        schema=target["schema"],
        table=target["table"],
    )
    sample_cols = _sample_columns(columns)
    sample_rows: list[dict[str, Any]] = []
    if sample_cols:
        sample_rows = await store.fetch_rows(
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
            columns=sample_cols,
            limit=5,
        )
    sample = _sample_payload(sample_rows)
    occupancy = _occupancy(columns, sample)
    business_cols = _business_columns(columns)
    business_sample = _business_sample(sample[:5], business_cols)
    hint = repository_name.strip() or workflow_name.strip()
    try:
        prompt = await chat_text(
            system=_SYSTEM,
            user=(
                f"Write a professional dashboard specification (max 100 lines). "
                f"Use normal headings (Business context, Objectives, KPI cards, Charts). "
                f"Do not use first-person headings like What I need or Who will use this.\n"
                f"Repository name: {hint or target.get('repository_name') or 'this repository'}\n"
                f"Workflow name: {workflow_name or target.get('workflow_name') or ''}\n"
                f"Business fields you may rely on (do not print this list in the output): {business_cols}\n"
                f"Which of those fields have values: { {k: occupancy.get(k) for k in business_cols} }\n"
                f"Example values (private; do not dump raw ids): {business_sample}\n"
            ),
            model="gpt-4.1-mini",
            max_completion_tokens=500,
        )
        lines = [line.rstrip() for line in prompt.splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) > 100:
            prompt = "\n".join(lines[:100]).rstrip()
        else:
            prompt = "\n".join(lines)
    except LLMError:
        raise
    except Exception as exc:
        logger.warning("dashboard_prompt_failed: %s", exc)
        raise LLMError(str(exc)) from exc

    wf_id, wf_name = _workflow_fields_for_response(repository_id=repository_id, target=target)
    return {
        "prompt": prompt,
        "tenant_id": target["tenant_id"],
        "repository_id": target["repository_id"],
        "repository_name": target.get("repository_name"),
        "workflow_id": wf_id,
        "workflow_name": wf_name,
        "table": target.get("qualified_table"),
    }
