"""Suggest a dashboard message from a tenant repository or workflow."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.dashboard_agent import _sample_columns, _sample_payload
from app.llm import LLMError, chat_json
from app.propose import _occupancy
from app.store import DashboardStore

logger = logging.getLogger("v6_dashboard.prompts")

_SYSTEM = """You write ONE dashboard request the user can paste as message on POST /dashboard/schema.

Return JSON only:
{"prompt":"one or two sentences"}

The prompt must fit THIS repository's columns. Different tables MUST produce different prompts.
- Name the repository if it helps (Accounts Payable, HR files, etc.).
- Mention only metrics that the columns can support.
- InvoiceAmount / Amount / InvoiceTotal are money. Never use FileSize for payable, paid, outstanding, or overdue.
- If Status occupancy is 0 and MatchedStatus has values, talk about match status, not invoice Status.
- Aging needs DueDate. Paid vs outstanding needs MatchedStatus.
- Do not mention HTML, SQL, widgets JSON, or API names.
- Do not invent columns.
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

    parts: list[str] = [f"Build a {name} dashboard"]
    if money:
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
        parts.append("with " + ", ".join(bits) + ".")
    elif department or filetype:
        bits = []
        if department:
            bits.append("counts by department")
        if filetype:
            bits.append("file type mix")
        parts.append("with " + (" and ".join(bits) if bits else "record counts") + ".")
    else:
        parts.append("with record counts and the main category breakdowns from this table.")
    return " ".join(parts)


async def generate_prompt(
    *,
    store: DashboardStore,
    tenant_id: str | None,
    repository_id: str | None = None,
    workflow_id: str | None = None,
    repository_name: str = "",
    workflow_name: str = "",
) -> dict[str, Any]:
    target = await asyncio.to_thread(
        store.resolve_target,
        tenant_id=tenant_id,
        repository_id=repository_id,
        workflow_id=workflow_id,
    )
    if repository_name.strip() and not target.get("repository_name"):
        target["repository_name"] = repository_name.strip()
    if workflow_name.strip() and not target.get("workflow_name"):
        target["workflow_name"] = workflow_name.strip()

    columns = await asyncio.to_thread(
        store.list_columns,
        tenant_id=target["tenant_id"],
        schema=target["schema"],
        table=target["table"],
    )
    sample_cols = _sample_columns(columns)
    sample_rows: list[dict[str, Any]] = []
    if sample_cols:
        sample_rows = await asyncio.to_thread(
            store.fetch_rows,
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
            columns=sample_cols,
        )
    sample = _sample_payload(sample_rows)
    occupancy = _occupancy(columns, sample)
    hint = repository_name.strip() or workflow_name.strip()
    fallback = fallback_prompt(target=target, columns=columns, occupancy=occupancy, name_hint=hint)
    prompt = fallback
    try:
        payload = await chat_json(
            system=_SYSTEM,
            user=(
                f"Write one dashboard message for this table.\n"
                f"Repository name hint: {hint or target.get('repository_name') or ''}\n"
                f"Workflow name hint: {workflow_name or target.get('workflow_name') or ''}\n"
                f"Repository: {target.get('repository_name') or target.get('repository_id')}\n"
                f"Workflow: {target.get('workflow_name') or target.get('workflow_id') or ''}\n"
                f"Table: {target.get('qualified_table')}\n"
                f"Columns: {columns}\n"
                f"Column occupancy (percent non-empty): {occupancy}\n"
                f"Sample rows: {sample[:8]}\n"
            ),
            timeout=30.0,
            temperature=0.4,
        )
        text = str(payload.get("prompt") or payload.get("message") or "").strip()
        if text:
            prompt = text[:500]
    except LLMError as exc:
        logger.warning("dashboard_prompt_failed: %s", exc)
    except Exception as exc:
        logger.warning("dashboard_prompt_failed: %s", exc)

    return {
        "prompt": prompt,
        "tenant_id": target["tenant_id"],
        "repository_id": target["repository_id"],
        "repository_name": target.get("repository_name"),
        "workflow_id": target.get("workflow_id"),
        "workflow_name": target.get("workflow_name"),
        "table": target.get("qualified_table"),
    }
