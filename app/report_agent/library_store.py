"""Persist generated report prompts as library cards (Draft until published)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger("orchestrator.report_agent.library")

_MIGRATION = Path(__file__).resolve().parents[2] / "db" / "migrations" / "0013_create_saved_reports.sql"

_STATUSES = frozenset({"Draft", "Published", "Pending Review", "Rejected"})
_VISIBILITY = frozenset(
    {"Private", "Selected Users", "Selected Groups", "Department", "Organization-wide"}
)


def _fmt_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def infer_domain(
    *,
    report_type: Optional[str] = None,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    if (domain or "").strip():
        return domain.strip()
    blob = f"{workflow_name or ''} {repository_name or ''}".lower()
    if any(token in blob for token in ("pay", "invoice", "ap ", "accounts payable")):
        return "Accounts Payable"
    rt = (report_type or "").strip().lower()
    if "repositor" in rt or (repository_name or "").strip():
        return "Document Management"
    if rt == "all_workflows":
        return "Workflow Automation"
    return (workflow_name or repository_name or "Accounts Payable").strip() or "Accounts Payable"


def row_to_card(row: Any) -> dict[str, Any]:
    get = row.get if isinstance(row, dict) else row.__getitem__
    schedule = get("schedule")
    return {
        "id": str(get("id")),
        "name": get("name"),
        "description": get("description") or "",
        "domain": get("domain") or "",
        "owner": get("owner") or "",
        "visibility": get("visibility") or "Private",
        "tags": _tags(get("tags")),
        "status": get("status") or "Draft",
        "favorite": bool(get("favorite")),
        "views": int(get("views") or 0),
        "runs": int(get("runs") or 0),
        "scheduled": bool(schedule),
        "version": int(get("version") or 1),
        "viz": get("viz") or "table",
        "createdAt": _fmt_dt(get("created_at")),
        "modifiedAt": _fmt_dt(get("modified_at")),
        "lastGeneratedAt": _fmt_dt(get("last_generated_at")),
    }


def row_to_detail(row: Any) -> dict[str, Any]:
    get = row.get if isinstance(row, dict) else row.__getitem__
    spec = get("report_spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError:
            spec = {}
    out = row_to_card(row)
    out.update(
        {
            "reportType": get("report_type"),
            "workflowName": get("workflow_name"),
            "repositoryName": get("repository_name"),
            "tenantId": get("tenant_id") or "",
            "reportPrompt": get("report_prompt") or "",
            "reportSpec": spec or {},
            "schedule": get("schedule"),
        }
    )
    return out


class ReportLibraryStore:
    def __init__(self, db_pool: Any = None):
        self._db = db_pool

    async def ensure_schema(self) -> None:
        if self._db is None:
            return
        sql = _MIGRATION.read_text(encoding="utf-8")
        buf: list[str] = []
        for raw_line in sql.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("--"):
                continue
            buf.append(raw_line)
            if line.endswith(";"):
                stmt = "\n".join(buf).strip().rstrip(";")
                if stmt:
                    await self._db.execute(stmt)
                buf = []
        tail = "\n".join(buf).strip().rstrip(";")
        if tail:
            await self._db.execute(tail)

    async def list_reports(
        self,
        *,
        tenant_id: str = "",
        status: Optional[str] = None,
        domain: Optional[str] = None,
        q: Optional[str] = None,
        favorite_only: bool = False,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("Report library database is not configured.")
        clauses = ["tenant_id = $1"]
        args: list[Any] = [tenant_id or ""]
        if status and status != "all":
            args.append(status)
            clauses.append(f"status = ${len(args)}")
        if domain and domain != "all":
            args.append(domain)
            clauses.append(f"domain = ${len(args)}")
        if favorite_only:
            clauses.append("favorite = TRUE")
        if (q or "").strip():
            args.append(f"%{q.strip()}%")
            clauses.append(
                f"(name ILIKE ${len(args)} OR description ILIKE ${len(args)} OR array_to_string(tags, ' ') ILIKE ${len(args)})"
            )
        sql = (
            "SELECT id, name, description, domain, owner, visibility, tags, status, "
            "favorite, views, runs, version, viz, schedule, created_at, modified_at, last_generated_at "
            f"FROM saved_reports WHERE {' AND '.join(clauses)} "
            "ORDER BY favorite DESC, modified_at DESC"
        )
        rows = await self._db.fetch(sql, *args)
        return [row_to_card(r) for r in rows]

    async def get_report(self, report_id: str, *, tenant_id: str = "") -> Optional[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("Report library database is not configured.")
        row = await self._db.fetchrow(
            "SELECT * FROM saved_reports WHERE id = $1 AND tenant_id = $2",
            UUID(str(report_id)),
            tenant_id or "",
        )
        return row_to_detail(row) if row else None

    async def save_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._db is None:
            raise RuntimeError("Report library database is not configured.")
        tenant_id = str(payload.get("tenantId") or payload.get("tenant_id") or "")
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        if not name or not description:
            raise ValueError("name and description are required.")
        report_type = str(payload.get("reportType") or payload.get("report_type") or "all_workflows")
        workflow_name = payload.get("workflowName") or payload.get("workflow_name")
        repository_name = payload.get("repositoryName") or payload.get("repository_name")
        domain = infer_domain(
            report_type=report_type,
            workflow_name=workflow_name,
            repository_name=repository_name,
            domain=payload.get("domain"),
        )
        visibility = str(payload.get("visibility") or "Private")
        if visibility not in _VISIBILITY:
            visibility = "Private"
        status = str(payload.get("status") or "Draft")
        if status not in _STATUSES:
            status = "Draft"
        spec = payload.get("reportSpec") or payload.get("report_spec") or {}
        if hasattr(spec, "model_dump"):
            spec = spec.model_dump(by_alias=True)
        tags = _tags(payload.get("tags"))
        row = await self._db.fetchrow(
            """
            INSERT INTO saved_reports (
                tenant_id, name, description, domain, report_type, workflow_name, repository_name,
                owner, visibility, tags, status, viz, report_prompt, report_spec, last_generated_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb, now()
            )
            RETURNING *
            """,
            tenant_id,
            name,
            description,
            domain,
            report_type,
            workflow_name,
            repository_name,
            str(payload.get("owner") or "console"),
            visibility,
            tags,
            status,
            str(payload.get("viz") or "table"),
            str(payload.get("reportPrompt") or payload.get("report_prompt") or ""),
            json.dumps(spec),
        )
        return row_to_detail(row)

    async def update_report(
        self,
        report_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str = "",
    ) -> Optional[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("Report library database is not configured.")
        current = await self.get_report(report_id, tenant_id=tenant_id)
        if current is None:
            return None
        name = str(payload.get("name") or current["name"]).strip()
        description = str(payload.get("description") or current["description"]).strip()
        visibility = str(payload.get("visibility") or current["visibility"])
        if visibility not in _VISIBILITY:
            visibility = current["visibility"]
        status = str(payload.get("status") or current["status"])
        if status not in _STATUSES:
            status = current["status"]
        favorite = payload.get("favorite")
        if favorite is None:
            favorite = current["favorite"]
        spec = payload.get("reportSpec") or payload.get("report_spec") or current.get("reportSpec") or {}
        if hasattr(spec, "model_dump"):
            spec = spec.model_dump(by_alias=True)
        prompt = payload.get("reportPrompt") or payload.get("report_prompt") or current.get("reportPrompt") or ""
        tags = _tags(payload["tags"]) if "tags" in payload else current.get("tags") or []
        row = await self._db.fetchrow(
            """
            UPDATE saved_reports SET
                name=$3, description=$4, visibility=$5, status=$6, favorite=$7,
                tags=$8, report_prompt=$9, report_spec=$10::jsonb,
                version=version+1, modified_at=now()
            WHERE id=$1 AND tenant_id=$2
            RETURNING *
            """,
            UUID(str(report_id)),
            tenant_id or "",
            name,
            description,
            visibility,
            status,
            bool(favorite),
            tags,
            prompt,
            json.dumps(spec),
        )
        return row_to_detail(row) if row else None

    async def touch_run(self, report_id: str, *, tenant_id: str = "") -> None:
        if self._db is None:
            return
        await self._db.execute(
            "UPDATE saved_reports SET runs = runs + 1, views = views + 1, last_generated_at = now() "
            "WHERE id=$1 AND tenant_id=$2",
            UUID(str(report_id)),
            tenant_id or "",
        )

    async def delete_report(self, report_id: str, *, tenant_id: str = "") -> bool:
        if self._db is None:
            raise RuntimeError("Report library database is not configured.")
        result = await self._db.execute(
            "DELETE FROM saved_reports WHERE id=$1 AND tenant_id=$2",
            UUID(str(report_id)),
            tenant_id or "",
        )
        return str(result).endswith("1")
