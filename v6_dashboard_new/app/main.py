"""FastAPI app: dashboard agent against SQL Server tenant DBs."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.dashboard_agent import DashboardAgent
from app.models import (
    ChatRequest,
    ChatResponse,
    DashboardDataRequest,
    DashboardSchemaRequest,
    PromptRequest,
    PromptResponse,
)
from app.prompts import generate_prompt
from app.sql import AzureSqlConnectionError, list_catalog_tenants, probe_catalog
from app.store import DashboardStore, DashboardStoreUnavailableError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("v6_dashboard")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(
    title="v6_dashboard",
    description="POST /prompts suggests a dashboard message. POST /dashboard/schema proposes widgets. POST /dashboard/data returns text/html.",
    version="1.0.0",
)
app.state.store = DashboardStore()
app.state.agent = DashboardAgent(app.state.store)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _detail_message(detail: object) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                msg = str(item.get("msg") or "Invalid request.")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts) or "Invalid request."
    return str(detail)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _detail_message(exc.errors())})


@app.get("/health")
def health() -> dict:
    sql: dict = {"ok": False}
    try:
        sql = probe_catalog()
    except AzureSqlConnectionError as exc:
        sql = {"ok": False, "error": str(exc)}
    except Exception as exc:
        sql = {"ok": False, "error": type(exc).__name__}
    return {
        "status": "ok" if sql.get("ok") else "degraded",
        "data_source": "sqlserver",
        "default_tenant_id": settings.azure_sql_default_tenant_id,
        "sql": sql,
    }


@app.get("/console")
def console() -> FileResponse:
    path = STATIC_DIR / "console.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="console.html is missing.")
    return FileResponse(path)


@app.get("/config")
def public_config() -> dict:
    return {
        "default_tenant_id": settings.azure_sql_default_tenant_id,
        "sql_server": settings.azure_sql_server,
        "catalog_database": settings.azure_sql_catalog_database,
    }


@app.get("/tenants")
def tenants() -> dict:
    try:
        items = list_catalog_tenants()
    except AzureSqlConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    default_id = settings.azure_sql_default_tenant_id
    selected = next(
        (row["id"] for row in items if row["id"].upper() == default_id.upper() and row["available"]),
        None,
    )
    if not selected:
        selected = next((row["id"] for row in items if row["available"]), default_id)
    return {"default_tenant_id": default_id, "selected_tenant_id": selected, "tenants": items}


@app.get("/targets")
def list_targets(tenant_id: str | None = None) -> dict:
    tid = (tenant_id or settings.azure_sql_default_tenant_id).strip()
    store: DashboardStore = app.state.store
    try:
        return store.list_targets(tenant_id=tid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DashboardStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dashboard store is currently unavailable, please try again.",
        ) from exc


async def _run_dashboard(
    *,
    request: Request,
    session_id: str,
    message: str,
    tenant_id: str | None,
    repository_id: str | None,
    workflow_id: str | None,
    dashboard_json: dict | None,
) -> ChatResponse:
    started = time.perf_counter()
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    agent: DashboardAgent = app.state.agent
    try:
        result = await agent.handle(
            session_id=session_id,
            message=(message or "").strip() or "I need a dashboard.",
            document_job={
                "tenant_id": tenant_id,
                "repository_id": repository_id,
                "workflow_id": workflow_id,
                "dashboard_json": dashboard_json,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DashboardStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dashboard store is currently unavailable, please try again.",
        ) from exc
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return ChatResponse(
        session_id=session_id,
        reply=result["reply"],
        correlation_id=correlation_id,
        latency_ms=latency_ms,
        dashboard_result=result.get("dashboard_result"),
        html=result.get("html"),
    )


@app.post(
    "/prompts",
    response_model=PromptResponse,
    summary="Suggest a dashboard message from the repository items table",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "by_repository": {
                            "summary": "tenant_id + repository_id",
                            "value": {
                                "session_id": "demo",
                                "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
                                "repository_name": "Accounts Payable",
                            },
                        },
                        "by_workflow": {
                            "summary": "tenant_id + workflow_id (repo is resolved)",
                            "value": {
                                "session_id": "demo",
                                "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                "workflow_id": "11111111-1111-1111-1111-111111111111",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def suggest_prompt(payload: PromptRequest, request: Request) -> PromptResponse:
    started = time.perf_counter()
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    store: DashboardStore = app.state.store
    try:
        result = await generate_prompt(
            store=store,
            tenant_id=payload.tenant_id,
            repository_id=payload.repository_id,
            workflow_id=payload.workflow_id,
            repository_name=payload.repository_name or "",
            workflow_name=payload.workflow_name or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DashboardStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dashboard store is currently unavailable, please try again.",
        ) from exc
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return PromptResponse(
        session_id=payload.session_id,
        prompt=result["prompt"],
        correlation_id=correlation_id,
        latency_ms=latency_ms,
        tenant_id=result.get("tenant_id"),
        repository_id=result.get("repository_id"),
        repository_name=result.get("repository_name"),
        workflow_id=result.get("workflow_id"),
        workflow_name=result.get("workflow_name"),
        table=result.get("table"),
    )


@app.post(
    "/dashboard/schema",
    response_model=ChatResponse,
    summary="Propose KPIs and charts",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "by_repository": {
                            "summary": "tenant_id + repository_id",
                            "value": {
                                "session_id": "demo",
                                "message": "I need an AP dashboard",
                                "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
                            },
                        },
                        "by_workflow": {
                            "summary": "tenant_id + workflow_id (repo is resolved)",
                            "value": {
                                "session_id": "demo",
                                "message": "I need a dashboard",
                                "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                "workflow_id": "11111111-1111-1111-1111-111111111111",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def dashboard_schema(payload: DashboardSchemaRequest, request: Request) -> ChatResponse:
    return await _run_dashboard(
        request=request,
        session_id=payload.session_id,
        message=(payload.message or "").strip() or "I need a dashboard.",
        tenant_id=payload.tenant_id,
        repository_id=payload.repository_id,
        workflow_id=payload.workflow_id,
        dashboard_json=None,
    )


@app.post(
    "/dashboard/data",
    response_class=HTMLResponse,
    summary="Hydrate edited widgets and return formatted HTML",
    responses={
        200: {
            "description": "Dashboard HTML. Set innerHTML from response.text — not JSON.",
            "content": {"text/html": {"example": "<style>\n.ez-dash{...}\n</style>\n<div class=\"ez-dash\">...</div>\n"}},
        }
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "hydrate": {
                            "summary": "Send edited schema JSON from /dashboard/schema",
                            "value": {
                                "session_id": "demo",
                                "message": "apply",
                                "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
                                "dashboard_json": {
                                    "phase": "schema",
                                    "kpis": [
                                        {
                                            "id": "total_ap",
                                            "label": "TOTAL AP",
                                            "description": "Total accounts payable amount across invoices.",
                                            "enabled": True,
                                            "agg": "sum",
                                            "columns": {"value": "InvoiceAmount"},
                                            "order": 1,
                                            "position": "top",
                                            "color": "#8b5cf6",
                                        },
                                        {
                                            "id": "overdue",
                                            "label": "OVERDUE",
                                            "description": "Total amount of invoices past their due date.",
                                            "enabled": True,
                                            "agg": "overdue_sum",
                                            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
                                            "order": 2,
                                            "position": "top",
                                            "color": "#ef4444",
                                        },
                                        {
                                            "id": "open_invoices",
                                            "label": "OPEN INVOICES",
                                            "description": "Count of open unpaid invoice records.",
                                            "enabled": True,
                                            "agg": "count",
                                            "columns": {},
                                            "order": 3,
                                            "position": "top",
                                            "color": "#8b5cf6",
                                        },
                                        {
                                            "id": "dpo",
                                            "label": "DPO",
                                            "description": "Days payable outstanding metric.",
                                            "enabled": True,
                                            "agg": "avg",
                                            "columns": {},
                                            "order": 4,
                                            "position": "top",
                                            "color": "#8b5cf6",
                                        },
                                    ],
                                    "charts": [
                                        {
                                            "id": "invoice_status",
                                            "title": "Invoice status",
                                            "label": "Invoice status",
                                            "description": "Record count grouped by Status.",
                                            "enabled": True,
                                            "type": "donut",
                                            "agg": "count",
                                            "grain": "none",
                                            "columns": {"group": "Status"},
                                            "order": 1,
                                            "position": "left",
                                            "span": 1,
                                            "color": "#7c5cff",
                                        }
                                    ],
                                    "data": None,
                                },
                            },
                        }
                    }
                }
            }
        }
    },
)
async def dashboard_data(payload: DashboardDataRequest, request: Request) -> HTMLResponse:
    result = await _run_dashboard(
        request=request,
        session_id=payload.session_id,
        message=(payload.message or "").strip() or "apply",
        tenant_id=payload.tenant_id,
        repository_id=payload.repository_id,
        workflow_id=payload.workflow_id,
        dashboard_json=payload.dashboard_json,
    )
    return HTMLResponse(content=result.html or "", media_type="text/html; charset=utf-8")


@app.post(
    "/chat",
    response_model=ChatResponse,
    summary="Combined schema+data (legacy). Prefer /dashboard/schema and /dashboard/data.",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "dashboard_schema": {
                            "summary": "Call 1 — propose widgets from tenant items table",
                            "value": {
                                "session_id": "demo",
                                "intent": "dashboard",
                                "message": "I need an AP dashboard",
                                "payload": {
                                    "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                    "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
                                },
                            },
                        },
                        "dashboard_data": {
                            "summary": "Call 2 — hydrate enabled widgets",
                            "value": {
                                "session_id": "demo",
                                "intent": "dashboard",
                                "message": "apply",
                                "payload": {
                                    "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                                    "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
                                    "dashboard_json": {
                                        "phase": "schema",
                                        "kpis": [{"id": "total_ap", "enabled": True}],
                                        "charts": [{"id": "supplier_risk", "enabled": True}],
                                    },
                                },
                            },
                        },
                    }
                }
            }
        }
    },
)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    body = payload.payload
    return await _run_dashboard(
        request=request,
        session_id=payload.session_id,
        message=(payload.message or "").strip() or "I need a dashboard.",
        tenant_id=body.tenant_id if body else None,
        repository_id=body.repository_id if body else None,
        workflow_id=body.workflow_id if body else None,
        dashboard_json=body.dashboard_json if body else None,
    )
