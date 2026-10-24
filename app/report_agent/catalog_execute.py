"""Run All Workflows / All Repositories reports from EZOFIS catalogs (no tenant SQL)."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Optional

from app.models.report_agent import ReportColumnOut, ReportSpec

VIRTUAL_CATALOG_TABLES = frozenset({"workflow_instances_all", "items_all"})


def is_virtual_catalog_table(table: Optional[str]) -> bool:
    return (table or "").lower() in VIRTUAL_CATALOG_TABLES


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _aging_bucket(when: Optional[date], today: Optional[date] = None) -> str:
    if when is None:
        return "Unknown"
    today = today or date.today()
    days = (today - when).days
    if days <= 5:
        return "0-5"
    if days <= 15:
        return "6-15"
    if days <= 30:
        return "16-30"
    return "30+"


def _sla_bucket(created: Optional[date], completed: Optional[date], today: Optional[date] = None) -> str:
    today = today or date.today()
    if completed is None:
        start = created or today
        days = (today - start).days
        return "Open" if days <= 5 else "Outside SLA"
    if created is None:
        return "Within SLA"
    days = (completed - created).days
    return "Within SLA" if days <= 5 else "Outside SLA"


def _col_outs(spec: ReportSpec) -> list[ReportColumnOut]:
    return [ReportColumnOut(key=c.key, label=c.label or c.key) for c in spec.columns]


def _paginate(rows: list[dict[str, Any]], page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    total = len(rows)
    start = max(0, (max(page, 1) - 1) * max(page_size, 1))
    return rows[start : start + max(page_size, 1)], total


def _keys(spec: ReportSpec) -> set[str]:
    return {c.key.lower() for c in spec.columns}


async def execute_catalog_spec(
    ezofis: Any,
    spec: ReportSpec,
    *,
    tenant_id: str,
    report_type: str,
    page: int = 1,
    page_size: int = 25,
) -> Optional[tuple[list[ReportColumnOut], list[dict[str, Any]], int, list[str]]]:
    table = (spec.source_table or "").lower()
    if report_type == "all_workflows" or table == "workflow_instances_all":
        rows = await _workflow_rows(ezofis, spec, tenant_id)
        page_rows, total = _paginate(rows, page, page_size)
        return (
            _col_outs(spec),
            page_rows,
            total,
            ["Counted live EZOFIS workflow instances across the tenant catalog."],
        )
    if report_type == "all_repositories" or table == "items_all":
        rows = await _repository_rows(ezofis, spec, tenant_id)
        page_rows, total = _paginate(rows, page, page_size)
        return (
            _col_outs(spec),
            page_rows,
            total,
            ["Counted live EZOFIS repository items across the tenant catalog."],
        )
    return None


async def _workflow_rows(ezofis: Any, spec: ReportSpec, tenant_id: str) -> list[dict[str, Any]]:
    listed = await ezofis.list_workflows(tenant_id)

    async def _one(item: dict[str, Any]) -> list[dict[str, Any]]:
        wf_id = str((item or {}).get("id") or "").strip()
        wf_name = str((item or {}).get("name") or "").strip()
        if not wf_id:
            return []
        instances = await ezofis.list_workflow_instances(tenant_id=tenant_id, workflow_id=wf_id)
        out: list[dict[str, Any]] = []
        for row in instances or []:
            created = _parse_date(row.get("createdAtUtc") or row.get("startedAtUtc"))
            completed = _parse_date(row.get("completedAtUtc"))
            out.append(
                {
                    "workflowName": row.get("workflowName") or wf_name,
                    "status": row.get("status") or "",
                    "startedBy": row.get("startedBy") or "",
                    "createdAtUtc": created,
                    "completedAtUtc": completed,
                    "open": completed is None,
                }
            )
        return out

    batches = await asyncio.gather(*[_one(item) for item in listed or []], return_exceptions=True)
    records: list[dict[str, Any]] = []
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        records.extend(batch)
    return _aggregate_workflow(spec, records)


async def _repository_rows(ezofis: Any, spec: ReportSpec, tenant_id: str) -> list[dict[str, Any]]:
    listed = await ezofis.list_repositories(tenant_id)

    async def _one(item: dict[str, Any]) -> list[dict[str, Any]]:
        repo_id = str((item or {}).get("id") or "").strip()
        repo_name = str((item or {}).get("name") or "").strip()
        if not repo_id:
            return []
        docs = await ezofis.list_repository_items(tenant_id=tenant_id, repository_id=repo_id)
        out: list[dict[str, Any]] = []
        for row in docs or []:
            out.append(
                {
                    "Repository_Name": repo_name,
                    "FileName": row.get("fileName") or row.get("FileName") or "",
                    "FileVersion": row.get("fileVersion") or row.get("FileVersion") or "",
                    "CreatedBy": row.get("createdBy") or row.get("CreatedBy") or "",
                    "CreatedAt": _parse_date(
                        row.get("createdAt") or row.get("documentDate") or row.get("createdAtUtc")
                    ),
                    "Status": row.get("status") or row.get("Status") or "",
                }
            )
        return out

    batches = await asyncio.gather(*[_one(item) for item in listed or []], return_exceptions=True)
    records: list[dict[str, Any]] = []
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        records.extend(batch)
    return _aggregate_repository(spec, records)


def _aggregate_workflow(spec: ReportSpec, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = _keys(spec)
    if "aging_bucket" in keys:
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        for row in records:
            if not row.get("open"):
                continue
            buckets[(_aging_bucket(row.get("createdAtUtc")), str(row.get("workflowName") or ""))] += 1
        out = []
        for (bucket, workflow), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
            out.append({"Aging_Bucket": bucket, "workflowName": workflow, "Request_Count": count})
        return out
    if "sla_bucket" in keys:
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        for row in records:
            buckets[(_sla_bucket(row.get("createdAtUtc"), row.get("completedAtUtc")), str(row.get("workflowName") or ""))] += 1
        out = []
        for (bucket, workflow), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
            out.append({"Sla_Bucket": bucket, "workflowName": workflow, "Request_Count": count})
        return out
    if "processed_count" in keys:
        buckets: dict[str, int] = defaultdict(int)
        for row in records:
            if row.get("open"):
                continue
            buckets[str(row.get("workflowName") or "")] += 1
        return [
            {"workflowName": name, "Processed_Count": count}
            for name, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0]))
        ]
    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for row in records:
        if not row.get("open"):
            continue
        buckets[(str(row.get("workflowName") or ""), str(row.get("status") or ""))] += 1
    return [
        {"workflowName": wf, "status": status, "Inbox_Count": count}
        for (wf, status), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    ]


def _aggregate_repository(spec: ReportSpec, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = _keys(spec)
    if "aging_bucket" in keys:
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        for row in records:
            buckets[(_aging_bucket(row.get("CreatedAt")), str(row.get("Repository_Name") or ""))] += 1
        return [
            {"Aging_Bucket": bucket, "Repository_Name": repo, "Request_Count": count}
            for (bucket, repo), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
        ]
    if "version_count" in keys:
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        for row in records:
            buckets[(str(row.get("Repository_Name") or ""), str(row.get("FileName") or ""))] += 1
        return [
            {"Repository_Name": repo, "FileName": name, "Version_Count": count}
            for (repo, name), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
        ]
    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for row in records:
        buckets[(str(row.get("Repository_Name") or ""), str(row.get("Status") or ""))] += 1
    return [
        {"Repository_Name": repo, "Status": status, "Document_Count": count}
        for (repo, status), count in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    ]
