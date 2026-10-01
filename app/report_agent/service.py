"""Report Agent service — Phase 1 prompt generation + Phase 2 LLM interpret + safe execute."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.catalog.tenant_llm import apply_tenant_agent_llm
from app.llm.adapter import LLMAdapter, LLMAdapterError
from app.models.report_agent import (
    AvailableFilterOut,
    GeneratePromptRequest,
    GeneratePromptResponse,
    ReportColumnOut,
    ReportTypeInfo,
    RunReportRequest,
    RunReportResponse,
    ScopeOption,
)
from app.report_agent.data_service import execute_report_query
from app.report_agent.definition_lock import lock_report_definition, parse_definition_json
from app.report_agent.definition_sql import generate_definition_sql
from app.report_agent.metadata_service import get_database_schema
from app.report_agent.pack import report_system_prompt
from app.report_agent.report_types import (
    get_report_type,
    list_report_types,
    validate_report_type_input,
)
from app.report_agent.schema_scope import (
    resolve_scope_options,
    schema_slice_to_prompt_block,
    scope_schema,
)
from app.report_agent.sql_validator import validate_read_only_sql

logger = logging.getLogger("orchestrator.report_agent")

_FALLBACK_PROMPT_SYSTEM = (
    "You are the EZOFIS Report Agent Phase 1. Return executable report prompt text only. "
    "Never invent schema. Never write SQL."
)
_FALLBACK_RUN_SYSTEM = (
    "You are the EZOFIS Report Agent Phase 2. Return locked reportDefinition JSON only. "
    "Never invent schema. Never write SQL."
)


class ReportAgentService:
    def __init__(
        self,
        tenant_pools: Any = None,
        db_pool: Any = None,
        catalog_store: Any = None,
        llm_adapter: Optional[LLMAdapter] = None,
        settings: Any = None,
    ):
        self._tenant_pools = tenant_pools
        self._db_pool = db_pool
        self._catalog_store = catalog_store
        self._llm = llm_adapter
        self._settings = settings

    def list_types(self) -> list[ReportTypeInfo]:
        return list_report_types()

    async def _resolve_db(self, tenant_id: Optional[str]) -> Any:
        tid = (tenant_id or "").strip()
        if tid and self._tenant_pools is not None:
            fn = getattr(self._tenant_pools, "acquire_for_global_search", None)
            if callable(fn):
                return await fn(tid, self._catalog_store)
            return await self._tenant_pools.acquire(tid)
        return self._db_pool

    async def _llm_overrides(
        self,
        tenant_id: Optional[str],
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        if model:
            overrides["model"] = model
            return overrides
        tid = (tenant_id or "").strip()
        if not tid or self._catalog_store is None:
            return overrides
        resolved = await apply_tenant_agent_llm(self._catalog_store, tid, "report")
        preset = resolved.get("overrides")
        if isinstance(preset, dict):
            overrides.update({k: v for k, v in preset.items() if v is not None})
        return overrides

    async def _chat(
        self,
        *,
        system: str,
        user: str,
        tenant_id: Optional[str],
        model: Optional[str],
    ) -> tuple[str, Optional[dict[str, Any]]]:
        if self._llm is None:
            raise RuntimeError("Report Agent LLM adapter is not configured.")
        overrides = await self._llm_overrides(tenant_id, model)
        try:
            result = await self._llm.chat_completion(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **overrides,
            )
        except LLMAdapterError as exc:
            raise RuntimeError(f"Report Agent model call failed: {exc}") from exc
        return str(result.get("content") or ""), result.get("usage")

    async def list_scope_options(
        self,
        *,
        scope: str,
        tenant_id: Optional[str],
    ) -> list[ScopeOption]:
        db = await self._resolve_db(tenant_id)
        if db is None:
            raise RuntimeError("Unable to access database metadata: No active database pool.")
        schema = await get_database_schema(db)
        return resolve_scope_options(schema, scope)

    async def generate_prompt(self, request: GeneratePromptRequest) -> GeneratePromptResponse:
        t0 = time.perf_counter()
        rt = validate_report_type_input(
            report_type=request.report_type,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
            description=request.description,
            require_description=True,
        )
        db = await self._resolve_db(request.tenant_id)
        if db is None:
            raise RuntimeError("Unable to access database metadata: No active database pool.")
        schema = await get_database_schema(db)
        tables = scope_schema(
            schema,
            rt,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
        )
        schema_block = schema_slice_to_prompt_block(tables)
        system = await report_system_prompt(
            "prompt",
            tenant_id=request.tenant_id,
            settings=self._settings,
            fallback=_FALLBACK_PROMPT_SYSTEM,
        )
        user_payload = {
            "reportType": rt.key,
            "description": (request.description or "").strip(),
            "workflowName": (request.workflow_name or "").strip() or None,
            "repositoryName": (request.repository_name or "").strip() or None,
        }
        user = (
            "Build an executable report-generation prompt for Phase 2.\n\n"
            f"Input:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"{schema_block}\n"
        )
        content, _usage = await self._chat(
            system=system,
            user=user,
            tenant_id=request.tenant_id,
            model=request.model,
        )
        prompt_text = (content or "").strip()
        if prompt_text.startswith("```"):
            prompt_text = prompt_text.strip("`")
            if prompt_text.lower().startswith("text"):
                prompt_text = prompt_text[4:].lstrip()
        warnings: list[str] = []
        if not tables:
            warnings.append("No schema tables matched this report type/scope.")
        if not prompt_text:
            raise RuntimeError("Model returned an empty report prompt.")
        title = (request.description or "").strip() or rt.name
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "report_agent_prompt_generated",
            extra={
                "report_type": rt.key,
                "tables": len(tables),
                "duration_ms": duration_ms,
            },
        )
        return GeneratePromptResponse(
            report_type=rt.key,
            title=title,
            report_prompt=prompt_text,
            warnings=warnings,
            schema_tables=[
                f"{t.schema_name + '.' if t.schema_name else ''}{t.table}" for t in tables
            ],
            duration_ms=duration_ms,
        )

    async def run_report(self, request: RunReportRequest) -> RunReportResponse:
        t0 = time.perf_counter()
        prompt = (request.report_prompt or "").strip()
        if not prompt:
            raise ValueError("reportPrompt is required.")

        rt = None
        if request.report_type:
            rt = validate_report_type_input(
                report_type=request.report_type,
                workflow_name=request.workflow_name,
                repository_name=request.repository_name,
                description="run",
                require_description=False,
            )
        elif request.workflow_name or request.repository_name:
            # infer type from scope params when omitted
            if request.workflow_name:
                rt = get_report_type("specific_workflow")
            elif request.repository_name:
                rt = get_report_type("specific_repository")

        db = await self._resolve_db(request.tenant_id)
        if db is None:
            raise RuntimeError("Unable to access database: No active database pool.")
        schema = await get_database_schema(db)

        if rt is not None:
            tables = scope_schema(
                schema,
                rt,
                workflow_name=request.workflow_name,
                repository_name=request.repository_name,
            )
        else:
            # broad slice when type omitted
            from app.report_agent.report_types import REPORT_TYPES

            tables = scope_schema(schema, REPORT_TYPES["all_workflows"])
            tables += scope_schema(schema, REPORT_TYPES["all_repositories"])
            # de-dupe
            seen: set[str] = set()
            deduped = []
            for t in tables:
                key = f"{t.schema_name}.{t.table}".lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(t)
            tables = deduped[:40]

        schema_block = schema_slice_to_prompt_block(tables)
        system = await report_system_prompt(
            "run",
            tenant_id=request.tenant_id,
            settings=self._settings,
            fallback=_FALLBACK_RUN_SYSTEM,
        )
        user_payload = {
            "reportType": rt.key if rt else request.report_type,
            "workflowName": (request.workflow_name or "").strip() or None,
            "repositoryName": (request.repository_name or "").strip() or None,
            "filters": request.filters or {},
            "page": request.page,
            "pageSize": request.page_size,
            "sort": request.sort.model_dump() if request.sort else None,
            "reportPrompt": prompt,
        }
        user = (
            "Interpret the report prompt into locked reportDefinition JSON.\n\n"
            f"Input:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"{schema_block}\n"
        )

        warnings: list[str] = []
        try:
            content, _usage = await self._chat(
                system=system,
                user=user,
                tenant_id=request.tenant_id,
                model=request.model,
            )
            raw_def = parse_definition_json(content)
        except Exception as exc:
            logger.warning("report_agent_run_llm_failed", extra={"error": str(exc)[:200]})
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title="Report",
                report_type=rt.key if rt else request.report_type,
                columns=[],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[],
                summary={},
                warnings=[f"Model failed to produce a report definition: {exc}"],
                duration_ms=duration_ms,
            )

        locked = lock_report_definition(
            raw_def,
            schema,
            report_type=rt.key if rt else request.report_type,
        )
        warnings.extend(locked.get("warnings") or [])

        if not locked.get("sources") or not locked.get("columns"):
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title=str(locked.get("title") or "Report"),
                report_type=locked.get("reportType"),
                columns=[],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[AvailableFilterOut(**f) for f in locked.get("availableFilters") or [] if f.get("key")],
                summary=locked.get("summary") or {},
                warnings=warnings or ["No executable definition after schema lock."],
                duration_ms=duration_ms,
                debug={"definition": locked} if request.include_debug else None,
            )

        sort_field = request.sort.field if request.sort else None
        sort_dir = request.sort.direction if request.sort else "asc"
        try:
            data_sql, count_sql = generate_definition_sql(
                locked,
                page=request.page,
                page_size=request.page_size,
                extra_filters=request.filters,
                sort_field=sort_field,
                sort_direction=sort_dir or "asc",
            )
        except ValueError as exc:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title=str(locked.get("title") or "Report"),
                report_type=locked.get("reportType"),
                columns=[],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[],
                summary={},
                warnings=warnings + [str(exc)],
                duration_ms=duration_ms,
                debug={"definition": locked} if request.include_debug else None,
            )

        allowed = [
            f"{(s.get('schemaName') + '.') if s.get('schemaName') else ''}{s.get('table')}"
            for s in locked.get("sources") or []
        ]
        ok, sql_errors = validate_read_only_sql(
            data_sql,
            schema=schema,
            allowed_tables=allowed,
            max_limit=500,
        )
        if not ok:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title=str(locked.get("title") or "Report"),
                report_type=locked.get("reportType"),
                columns=[],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[],
                summary={},
                warnings=warnings + sql_errors,
                duration_ms=duration_ms,
                debug={"definition": locked, "sql": data_sql, "sqlErrors": sql_errors}
                if request.include_debug
                else None,
            )

        try:
            report_data = await execute_report_query(db, data_sql, timeout_sec=15.0)
            total_count = report_data.row_count
            try:
                count_rows = await db.fetch(count_sql)
                if count_rows:
                    row0 = count_rows[0]
                    total_count = int(row0["total"] if hasattr(row0, "keys") else row0[0])
            except Exception as exc:
                warnings.append(f"Count query failed; using page row count. ({exc})")
                total_count = report_data.row_count
        except Exception as exc:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title=str(locked.get("title") or "Report"),
                report_type=locked.get("reportType"),
                columns=[
                    ReportColumnOut(key=c["key"], label=c.get("label") or c["key"])
                    for c in locked.get("columns") or []
                ],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[
                    AvailableFilterOut(
                        key=f.get("key") or "",
                        label=f.get("label") or f.get("key") or "",
                        field=f.get("field"),
                        type=f.get("type") or "text",
                    )
                    for f in locked.get("availableFilters") or []
                    if f.get("key")
                ],
                summary=locked.get("summary") or {},
                warnings=warnings + [f"Query execution failed: {exc}"],
                duration_ms=duration_ms,
                debug={"definition": locked, "sql": data_sql} if request.include_debug else None,
            )

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        columns_out = [
            ReportColumnOut(key=c["key"], label=c.get("label") or c["key"])
            for c in locked.get("columns") or []
        ]
        # Prefer definition column order; fall back to query columns
        if not columns_out and report_data.columns:
            columns_out = [ReportColumnOut(key=c, label=c) for c in report_data.columns]

        logger.info(
            "report_agent_run_completed",
            extra={
                "report_type": locked.get("reportType"),
                "rows": len(report_data.rows),
                "total_count": total_count,
                "duration_ms": duration_ms,
            },
        )
        return RunReportResponse(
            title=str(locked.get("title") or "Report"),
            report_type=locked.get("reportType"),
            columns=columns_out,
            rows=report_data.rows,
            total_count=total_count,
            page=request.page,
            page_size=request.page_size,
            filters=[
                AvailableFilterOut(
                    key=f.get("key") or "",
                    label=f.get("label") or f.get("key") or "",
                    field=f.get("field"),
                    type=f.get("type") or "text",
                )
                for f in locked.get("availableFilters") or []
                if f.get("key")
            ],
            summary=locked.get("summary") or {},
            warnings=warnings,
            duration_ms=duration_ms,
            debug={"definition": locked, "sql": data_sql, "countSql": count_sql}
            if request.include_debug
            else None,
        )
