"""Dashboard agent — two /chat calls, one intent.

Call 1 (no dashboard_json): tenant_id + repository_id or workflow_id + a
short user message → schema JSON of possible KPIs/charts (data is null).

Call 2 (dashboard_json present): same JSON, edited enabled flags → query
the resolved items table and fill data.
"""
from __future__ import annotations

from typing import Any, Optional

from app.dashboard.store import DashboardStore
from app.dashboard.widgets import (
    amounts_missing,
    hydrate_data,
    overlay_extract_artifacts,
    propose_widgets,
    row_id,
)

_SCHEMA_REPLY = "Suggested dashboard. Enable or disable widgets, then send this JSON back."
_DATA_REPLY = "Dashboard data loaded."


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _copy_widgets(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            out.append(dict(item))
    return out


class DashboardAgent:
    def __init__(self, store: DashboardStore):
        self._store = store

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        job = document_job or {}
        dashboard_json = job.get("dashboard_json") if isinstance(job.get("dashboard_json"), dict) else None
        tenant_id = (job.get("tenant_id") or "").strip()
        repository_id = (job.get("repository_id") or "").strip() or None
        workflow_id = (job.get("workflow_id") or "").strip() or None

        if dashboard_json:
            tenant_id = tenant_id or str(dashboard_json.get("tenant_id") or "").strip()
            repository_id = repository_id or (str(dashboard_json.get("repository_id") or "").strip() or None)
            workflow_id = workflow_id or (str(dashboard_json.get("workflow_id") or "").strip() or None)

        target = await self._store.resolve_target(
            tenant_id=tenant_id,
            repository_id=repository_id,
            workflow_id=workflow_id,
        )
        columns = await self._store.list_columns(
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
        )
        proposed_kpis, proposed_charts, bound = propose_widgets(columns)
        workflow_slug = _workflow_slug(message, target)

        if not dashboard_json:
            result = {
                "phase": "schema",
                "workflow": workflow_slug,
                "tenant_id": target["tenant_id"],
                "repository_id": target["repository_id"],
                "repository_name": target.get("repository_name"),
                "workflow_id": target.get("workflow_id"),
                "table": target["qualified_table"],
                "columns": columns,
                "kpis": proposed_kpis,
                "charts": proposed_charts,
                "data": None,
            }
            return {"reply": _SCHEMA_REPLY, "usage": None, "dashboard_result": result}

        kpis = _merge_enabled(proposed_kpis, dashboard_json.get("kpis"))
        charts = _merge_enabled(proposed_charts, dashboard_json.get("charts"))
        needed = sorted({col for item in kpis + charts for col in _as_dict(item.get("columns")).values() if col})
        extra = [name for name in columns if name.lower() in {"id", "file_name", "is_deleted", "currency"}]
        fetch_cols = list(dict.fromkeys(needed + extra))
        rows = await self._store.fetch_rows(
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
            columns=fetch_cols,
        )
        data_source = "items_table"
        if amounts_missing(rows, bound):
            artifacts = await self._store.fetch_extract_artifacts(
                tenant_id=target["tenant_id"],
                item_keys=[row_id(row) for row in rows],
            )
            rows, used = overlay_extract_artifacts(rows, bound, artifacts)
            if used:
                data_source = "ap_extract"
        data = hydrate_data(rows=rows, bound=bound, kpis=kpis, charts=charts)
        result = {
            "phase": "data",
            "workflow": workflow_slug,
            "tenant_id": target["tenant_id"],
            "repository_id": target["repository_id"],
            "repository_name": target.get("repository_name"),
            "workflow_id": target.get("workflow_id"),
            "table": target["qualified_table"],
            "kpis": kpis,
            "charts": charts,
            "data": data,
            "data_source": data_source,
        }
        return {"reply": _DATA_REPLY, "usage": None, "dashboard_result": result}


def _workflow_slug(message: str, target: dict[str, Any]) -> str:
    blob = " ".join(
        part
        for part in (
            message or "",
            str(target.get("repository_name") or ""),
            str(target.get("workflow_name") or ""),
        )
        if part
    ).lower()
    if "ap" in blob.split() or "payable" in blob or "invoice" in blob:
        return "ap"
    name = (target.get("repository_name") or target.get("workflow_name") or "dashboard").strip()
    return name.lower().replace(" ", "_")[:40] or "dashboard"


def _merge_enabled(proposed: list[dict[str, Any]], incoming: Any) -> list[dict[str, Any]]:
    incoming_map = {
        str(item.get("id")): item
        for item in _copy_widgets(incoming)
        if item.get("id")
    }
    merged: list[dict[str, Any]] = []
    for item in proposed:
        widget_id = str(item.get("id"))
        override = incoming_map.get(widget_id)
        row = dict(item)
        if override is not None and "enabled" in override:
            row["enabled"] = bool(override.get("enabled"))
        merged.append(row)
    return merged
