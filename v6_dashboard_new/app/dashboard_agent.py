"""Dashboard agent — two /chat calls, one intent, SQL Server store."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Optional

from app.insights import generate_insights
from app.propose import (
    apply_ask_limits,
    apply_default_layout,
    ensure_widget_descriptions,
    overlay_layout_from_message,
    propose_dashboard,
    propose_generic,
    unpack_proposal,
)
from app.render import render_dashboard_html
from app.store import DashboardStore
from app.widgets import (
    amounts_missing,
    attach_kpi_trends,
    bind_columns,
    hydrate_data,
    hydrate_from_spec,
    overlay_extract_artifacts,
    propose_widgets,
    rebind_sparse_group_columns,
    repair_live_spec,
    row_id,
    spec_has_agg,
)

_SCHEMA_REPLY = "Suggested dashboard. Enable or disable widgets, then send this JSON back."
_DATA_REPLY = "Dashboard data loaded."

Proposer = Callable[..., Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]]]]]

_SAMPLE_SKIP = {
    "ocrtext",
    "ocrjson",
    "summaryjson",
    "filepath",
    "storageproviderid",
}
_AP_SAMPLE_FIRST = {
    "invoiceamount",
    "amount",
    "duedate",
    "supplier",
    "vendor",
    "vendorname",
    "matchedstatus",
    "matchstatus",
    "invoicedate",
    "currency",
    "status",
    "aistatus",
}


def _alnum(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _sample_columns(columns: list[str]) -> list[str]:
    usable = [name for name in columns if name and _alnum(name) not in _SAMPLE_SKIP]
    first = [name for name in usable if _alnum(name) in _AP_SAMPLE_FIRST]
    rest = [name for name in usable if name not in first]
    return (first + rest)[:40]


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


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:120]


def _sample_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        slim: dict[str, Any] = {}
        for key, value in list(row.items())[:18]:
            slim[str(key)] = _json_safe(value)
        out.append(slim)
    return out


_GENERIC_MESSAGES = {"", "apply", "i need a dashboard", "i need a dashboard."}


def _job_message(message: str, dashboard_json: dict[str, Any] | None) -> str:
    """Keep the schema ask on the data call (frontends often send message=apply)."""
    text = (message or "").strip()
    if text and text.lower() not in _GENERIC_MESSAGES:
        return text
    blob = dashboard_json or {}
    for key in ("message", "prompt", "user_request"):
        stored = str(blob.get(key) or "").strip()
        if stored and stored.lower() not in _GENERIC_MESSAGES:
            return stored
    return text


class DashboardAgent:
    def __init__(self, store: DashboardStore, *, proposer: Proposer | None = None):
        self._store = store
        self._proposer = proposer or propose_dashboard

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
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

        target = await asyncio.to_thread(
            self._store.resolve_target,
            tenant_id=tenant_id,
            repository_id=repository_id,
            workflow_id=workflow_id,
        )
        columns = await asyncio.to_thread(
            self._store.list_columns,
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
        )
        workflow_slug = _workflow_slug(message, target)

        if not dashboard_json:
            sample_cols = _sample_columns(columns)
            sample_rows = []
            if sample_cols:
                sample_rows = await asyncio.to_thread(
                    self._store.fetch_rows,
                    tenant_id=target["tenant_id"],
                    schema=target["schema"],
                    table=target["table"],
                    columns=sample_cols,
                )
            proposed = await self._proposer(
                message=message,
                target=target,
                columns=columns,
                sample_rows=_sample_payload(sample_rows),
            )
            kpis, charts = unpack_proposal(proposed)
            kpis, charts = apply_ask_limits(message, kpis, charts)
            apply_default_layout(kpis, charts)
            ensure_widget_descriptions(kpis, charts)
            _ensure_widget_contract(kpis, charts)
            result = {
                "phase": "schema",
                "workflow": workflow_slug,
                "tenant_id": target["tenant_id"],
                "repository_id": target["repository_id"],
                "repository_name": target.get("repository_name"),
                "workflow_id": target.get("workflow_id"),
                "table": target["qualified_table"],
                "columns": columns,
                "message": message,
                "kpis": kpis,
                "charts": charts,
                "data": None,
            }
            return {"reply": _SCHEMA_REPLY, "dashboard_result": result}

        kpis = _copy_widgets(dashboard_json.get("kpis"))
        charts = _copy_widgets(dashboard_json.get("charts"))
        message = _job_message(message, dashboard_json)
        kpis, charts = apply_ask_limits(message, kpis, charts)
        _ensure_widget_contract(kpis, charts)
        repair_live_spec(kpis, charts, columns)
        bound = bind_columns(columns)
        use_spec = spec_has_agg(kpis, charts)
        if not use_spec:
            if any(str(item.get("id") or "") in _CLASSIC_IDS for item in kpis + charts):
                proposed_kpis, proposed_charts, bound = propose_widgets(columns)
                kpis = _merge_enabled(proposed_kpis, dashboard_json.get("kpis"))
                charts = _merge_enabled(proposed_charts, dashboard_json.get("charts"))
            else:
                proposed_kpis, proposed_charts = propose_generic(columns, message=message)
                kpis = _merge_enabled(proposed_kpis, dashboard_json.get("kpis"))
                charts = _merge_enabled(proposed_charts, dashboard_json.get("charts"))
                use_spec = True

        needed = sorted(
            {col for item in kpis + charts for col in _as_dict(item.get("columns")).values() if col}
        )
        extra = [
            name
            for name in columns
            if "".join(ch for ch in name.lower() if ch.isalnum())
            in {
                "id",
                "filename",
                "isdeleted",
                "currency",
                "status",
                "aistatus",
                "matchedstatus",
                "matchstatus",
                "supplier",
                "vendor",
                "vendorname",
                "invoicedate",
                "createdatutc",
                "createdat",
                "duedate",
                "docdate",
                "invoiceamount",
                "amount",
            }
        ]
        fetch_cols = list(dict.fromkeys(needed + extra))
        rows = await asyncio.to_thread(
            self._store.fetch_rows,
            tenant_id=target["tenant_id"],
            schema=target["schema"],
            table=target["table"],
            columns=fetch_cols,
        )
        data_source = "items_table"
        if amounts_missing(rows, bound):
            artifacts = await asyncio.to_thread(
                self._store.fetch_extract_artifacts,
                tenant_id=target["tenant_id"],
                item_keys=[row_id(row) for row in rows],
            )
            rows, used = overlay_extract_artifacts(rows, bound, artifacts)
            if used:
                data_source = "ap_extract"
        rebind_sparse_group_columns(charts, rows)
        if use_spec:
            data = hydrate_from_spec(rows=rows, kpis=kpis, charts=charts)
        else:
            data = hydrate_data(rows=rows, bound=bound, kpis=kpis, charts=charts)
        apply_default_layout(kpis, charts)
        overlay_layout_from_message(message, kpis, charts)
        kpi_data = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
        attach_kpi_trends(rows=rows, kpis=kpis, kpi_data=kpi_data)
        insights = await generate_insights(
            repository_name=str(target.get("repository_name") or "repository"),
            kpis=kpis,
            charts=charts,
            data=data,
            message=message,
        )
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
            "insights": insights,
            "data_source": data_source,
        }
        html = render_dashboard_html(result, message=message)
        return {"reply": _DATA_REPLY, "dashboard_result": result, "html": html}


_CLASSIC_IDS = {
    "total_ap",
    "overdue",
    "open_invoices",
    "average_invoice",
    "overdue_count",
    "overdue_pct",
    "current_ap",
    "due_in_30",
    "supplier_count",
    "unmatched_count",
    "dpo",
    "supplier_risk",
    "match_status",
    "profit_vs_ap",
    "ap_aging",
    "top_suppliers",
    "overdue_by_supplier",
    "invoices_by_status",
    "invoice_count_by_month",
    "currency_mix",
    "matched_vs_unmatched",
}


def _workflow_slug(message: str, target: dict[str, Any]) -> str:
    name = (target.get("repository_name") or target.get("workflow_name") or "dashboard").strip()
    slug = name.lower().replace(" ", "_")[:40]
    return slug or "dashboard"


def _ensure_widget_contract(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> None:
    """Keep KPI/chart objects on the documented frontend shape (never null columns)."""
    for kpi in kpis:
        if not isinstance(kpi.get("columns"), dict):
            kpi["columns"] = {}
        kpi.setdefault("label", str(kpi.get("id") or "KPI"))
        kpi.setdefault("enabled", True)
        kpi.setdefault("agg", "count")
        kpi.setdefault("description", "")
    for chart in charts:
        if not isinstance(chart.get("columns"), dict):
            chart["columns"] = {}
        title = str(chart.get("title") or chart.get("label") or chart.get("id") or "Chart")
        chart.setdefault("title", title)
        chart.setdefault("label", title)
        chart.setdefault("enabled", True)
        chart.setdefault("type", "donut")
        chart.setdefault("agg", "count")
        chart.setdefault("grain", "none")
        chart.setdefault("description", "")


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
