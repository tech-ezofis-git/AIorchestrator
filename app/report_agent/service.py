"""Report Agent service — orchestrates schema discovery, prompt generation, report planning, and data execution."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.models.report_agent import (
    DataQuery,
    DiscoveredField,
    DiscoveredSchemaSummary,
    GeneratePromptRequest,
    GeneratePromptResponse,
    GenerateReportPlanRequest,
    GenerateReportPlanResponse,
    ReportAgentTemplate,
    ReportData,
    ReportValidation,
)
from app.report_agent.data_service import execute_report_query, sample_table_values, verify_table_accessible
from app.report_agent.field_discovery import find_relevant_fields
from app.report_agent.metadata_service import get_database_schema
from app.report_agent.planner import create_report_plan
from app.report_agent.prompt_generator import generate_dynamic_prompt
from app.report_agent.prompt_parser import compile_prompt_intent, parse_prompt_metadata
from app.report_agent.report_validator import validate_report_data
from app.report_agent.sql_generator import generate_sql
from app.report_agent.sql_validator import validate_read_only_sql
from app.report_agent.templates import get_template, list_templates

logger = logging.getLogger("orchestrator.report_agent")


class ReportAgentService:
    """Orchestrates database metadata discovery, dynamic prompts, report plans, and query execution."""

    def __init__(self, tenant_pools: Any = None, db_pool: Any = None, catalog_store: Any = None):
        self._tenant_pools = tenant_pools
        self._db_pool = db_pool
        self._catalog_store = catalog_store

    def get_supported_templates(self) -> list[ReportAgentTemplate]:
        return list_templates()

    async def _resolve_db(self, tenant_id: Optional[str]) -> Any:
        tid = (tenant_id or "").strip()
        if tid and self._tenant_pools is not None:
            fn = getattr(self._tenant_pools, "acquire_for_global_search", None)
            if callable(fn):
                return await fn(tid, self._catalog_store)
            return await self._tenant_pools.acquire(tid)
        return self._db_pool

    async def generate_prompt(self, request: GeneratePromptRequest) -> GeneratePromptResponse:
        t0 = time.perf_counter()
        template_id = (request.template_id or "").strip().lower()
        template = get_template(template_id)
        if template is None:
            logger.warning(
                "report_agent_unsupported_template",
                extra={"template_id": request.template_id},
            )
            raise ValueError(f"Unsupported report template '{request.template_id}'.")

        # Resolve DB connection / pool
        db = await self._resolve_db(request.tenant_id)
        if db is None:
            logger.error("report_agent_db_unavailable", extra={"template_id": template.id})
            raise RuntimeError("Unable to access database metadata: No active database pool.")

        # 1. Database metadata discovery
        schema_t0 = time.perf_counter()
        try:
            schema = await get_database_schema(db)
        except Exception as exc:
            logger.error(
                "report_agent_schema_discovery_failed",
                extra={
                    "template_id": template.id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
            )
            raise RuntimeError(f"Unable to access database metadata: {type(exc).__name__}") from exc
        schema_duration_ms = round((time.perf_counter() - schema_t0) * 1000, 2)

        # 2. Relevant field discovery
        disc_t0 = time.perf_counter()
        discovered_tables, discovered_fields, missing_fields = find_relevant_fields(template, schema)
        disc_duration_ms = round((time.perf_counter() - disc_t0) * 1000, 2)

        # 3. Dynamic prompt generation
        prompt_t0 = time.perf_counter()
        prompt_text = generate_dynamic_prompt(
            template,
            discovered_tables,
            discovered_fields,
            missing_fields,
        )
        prompt_duration_ms = round((time.perf_counter() - prompt_t0) * 1000, 2)
        total_duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        # 4. Structured logging
        logger.info(
            "report_agent_prompt_generated",
            extra={
                "templateId": template.id,
                "template_title": template.title,
                "database_discovery_duration_ms": schema_duration_ms,
                "field_discovery_duration_ms": disc_duration_ms,
                "prompt_generation_duration_ms": prompt_duration_ms,
                "total_duration_ms": total_duration_ms,
                "tables_discovered_count": len(discovered_tables),
                "fields_discovered_count": len(discovered_fields),
                "missing_fields_count": len(missing_fields),
                "discovered_tables": discovered_tables,
            },
        )

        return GeneratePromptResponse(
            template_id=template.id,
            title=template.title,
            domain=template.domain,
            discovered_tables=discovered_tables,
            discovered_fields=discovered_fields,
            missing_fields=missing_fields,
            prompt=prompt_text,
        )

    async def generate_report_plan(
        self,
        request: GenerateReportPlanRequest,
    ) -> GenerateReportPlanResponse:
        """Phase 2: Inspect schema & data, build ReportPlan, generate safe SQL, execute, and validate."""
        t0 = time.perf_counter()
        # Extract template ID or title from request or prompt
        t_ident = (request.template_id or "").strip()
        prompt_title, prompt_tables, prompt_fields = parse_prompt_metadata(request.prompt or "")

        template = None
        if prompt_title:
            template = get_template(prompt_title)
        if template is None and t_ident:
            template = get_template(t_ident)

        if template is None:
            logger.warning(
                "report_agent_unsupported_template",
                extra={"template_id": request.template_id, "prompt_title": prompt_title},
            )
            raise ValueError(f"Unsupported report template '{request.template_id or prompt_title}'.")

        # Compile structured prompt intent from user prompt (business concepts, not SQL/identifiers)
        prompt_intent = compile_prompt_intent(request.prompt or "", template_title=template.title)

        # Resolve DB connection / pool
        db = await self._resolve_db(request.tenant_id)

        # 1. Live database schema discovery
        schema_t0 = time.perf_counter()
        if db is None:
            raise RuntimeError("Unable to generate a report plan: no active database connection.")
        try:
            schema = await get_database_schema(db)
        except Exception as exc:
            logger.warning(
                "report_agent_schema_discovery_failed",
                extra={"template_id": template.id, "error": str(exc)[:200]},
            )
            raise RuntimeError("Unable to generate a report plan: live database schema discovery failed.") from exc
        if not schema.tables:
            raise RuntimeError("Unable to generate a report plan: live database schema contains no reportable tables.")

        schema_duration_ms = round((time.perf_counter() - schema_t0) * 1000, 2)

        # 2. Re-discover or validate fields against live schema
        disc_tables, disc_fields, _ = find_relevant_fields(template, schema, max_tables=3)

        # Prioritize prompt-specified tables if valid in live schema
        candidate_tables: list[str] = []
        for pt in prompt_intent.requested_tables:
            pt_clean = pt.strip().replace('"', '')
            for st in schema.tables:
                st_ident = f"{st[0]}.{st[1]}" if st[0] != "public" else st[1]
                if pt_clean.lower() in (st[1].lower(), f"{st[0]}.{st[1]}".lower()):
                    if st_ident not in candidate_tables:
                        candidate_tables.append(st_ident)
        for dt in disc_tables:
            if dt not in candidate_tables:
                candidate_tables.append(dt)

        # 3. Verify table accessibility and inspect actual data on live DB connection
        accessible_tables: list[str] = []
        if db is not None:
            for t_cand in candidate_tables:
                if await verify_table_accessible(db, t_cand):
                    accessible_tables.append(t_cand)
            if not accessible_tables:
                raise RuntimeError("Unable to generate a report plan: no discovered report table is accessible.")
            disc_tables = accessible_tables

        primary_table = disc_tables[0] if disc_tables else (schema.tables[0][1] if schema.tables else "")

        # Get all live columns for the primary table from schema
        p_parts = primary_table.split(".")
        p_schema = p_parts[0] if len(p_parts) > 1 else None
        p_tbl = p_parts[-1]

        live_cols = schema.get_columns_for_table(p_schema or "public", p_tbl)
        if not live_cols and p_schema:
            live_cols = schema.get_columns_for_table(None, p_tbl)
        if not live_cols:
            live_cols = [c for c in schema.all_columns if c.table.lower() == p_tbl.lower()]

        live_col_map = {c.column.lower(): c for c in live_cols}

        # Validate requested fields from prompt against live schema
        req_fields = prompt_intent.requested_fields or [f.column for f in prompt_fields]
        validated_fields: list[DiscoveredField] = []

        if req_fields:
            for rf in req_fields:
                rf_clean = rf.strip().split(".")[-1].lower()
                if rf_clean in live_col_map:
                    c_meta = live_col_map[rf_clean]
                    # Avoid duplicates
                    if not any(vf.column.lower() == c_meta.column.lower() for vf in validated_fields):
                        validated_fields.append(
                            DiscoveredField(
                                table=primary_table,
                                column=c_meta.column,
                                type=c_meta.data_type,
                            )
                        )
                else:
                    prompt_intent.warnings.append(
                        f"Field '{rf}' specified in prompt was not found in table '{primary_table}'."
                    )

        if not validated_fields:
            # Filter default discovered fields to accessible tables
            acc_names = {t.lower() for t in accessible_tables} | {t.split(".")[-1].lower() for t in accessible_tables}
            validated_fields = [
                f for f in disc_fields
                if f.table.lower() in acc_names or f.table.split(".")[-1].lower() in acc_names
            ]
            if not validated_fields:
                validated_fields = disc_fields

        col_names = [
            f.column for f in validated_fields
            if f.table.lower() == primary_table.lower() or f.table.split(".")[-1].lower() == primary_table.split(".")[-1].lower()
        ]
        if not col_names:
            col_names = [f.column for f in validated_fields]

        sample_t0 = time.perf_counter()
        sampled_data = await sample_table_values(db, primary_table, col_names, limit=20) if db is not None else {}
        sample_duration_ms = round((time.perf_counter() - sample_t0) * 1000, 2)

        # 4. Generate ReportPlan
        plan_t0 = time.perf_counter()
        report_plan = create_report_plan(
            template=template,
            schema=schema,
            discovered_tables=disc_tables,
            discovered_fields=validated_fields,
            sampled_values=sampled_data,
            prompt_intent=prompt_intent,
        )
        plan_duration_ms = round((time.perf_counter() - plan_t0) * 1000, 2)

        # 5. Generate and validate safe SQL
        limit = request.limit or 50
        sql = generate_sql(report_plan, limit=limit)
        is_sql_valid, sql_errors = validate_read_only_sql(sql, schema=schema, allowed_tables=disc_tables)

        if not is_sql_valid:
            logger.error(
                "report_agent_sql_validation_error",
                extra={"template_id": template.id, "errors": sql_errors, "sql": sql},
            )
            # Re-generate minimal safe fallback SQL without calculations
            report_plan.calculations = []
            sql = generate_sql(report_plan, limit=limit)
            is_sql_valid, sql_errors = validate_read_only_sql(sql, schema=schema, allowed_tables=disc_tables)

        # 6. Execute SQL Query with retry loop (max 3 attempts)
        report_data = ReportData()
        exec_t0 = time.perf_counter()
        exec_error: Optional[str] = None

        for attempt in range(1, 4):
            try:
                report_data = await execute_report_query(db, sql, timeout_sec=15.0)
                exec_error = None
                break
            except Exception as exc:
                exec_error = str(exc)
                logger.warning(
                    "report_agent_query_attempt_failed",
                    extra={"attempt": attempt, "error": str(exc)[:200], "sql": sql[:200]},
                )
                if attempt == 1 and report_plan.calculations:
                    # Attempt 2: Remove complex expressions and retry
                    report_plan.calculations = []
                    sql = generate_sql(report_plan, limit=limit)
                elif attempt == 2 and report_plan.filters:
                    # Attempt 3: Remove complex filters and retry
                    report_plan.filters = []
                    sql = generate_sql(report_plan, limit=limit)

        exec_duration_ms = round((time.perf_counter() - exec_t0) * 1000, 2)

        # Ensure columns header is populated even if 0 rows returned
        if not report_data.columns and report_plan.columns:
            report_data.columns = [c.field for c in report_plan.columns] + [c.label for c in report_plan.calculations]

        # 7. Validate returned data against plan
        if exec_error:
            validation = ReportValidation(
                valid=False,
                errors=[f"Query execution failed: {exec_error}"],
                checks={"sql_safety": is_sql_valid, "execution_success": False},
            )
        else:
            validation = validate_report_data(
                report_plan=report_plan,
                report_data=report_data,
                sql_is_valid=is_sql_valid,
                sql_errors=sql_errors if not is_sql_valid else None,
            )

        if report_plan.warnings:
            for w in report_plan.warnings:
                if w not in validation.warnings:
                    validation.warnings.append(w)

        total_duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        logger.info(
            "report_agent_plan_generated",
            extra={
                "templateId": template.id,
                "table": report_plan.source.table,
                "row_count": report_data.row_count,
                "is_valid": validation.valid,
                "schema_duration_ms": schema_duration_ms,
                "sample_duration_ms": sample_duration_ms,
                "plan_duration_ms": plan_duration_ms,
                "exec_duration_ms": exec_duration_ms,
                "total_duration_ms": total_duration_ms,
            },
        )

        return GenerateReportPlanResponse(
            template_id=template.id,
            report_plan=report_plan,
            database_schema=DiscoveredSchemaSummary(
                tables=disc_tables,
                fields=validated_fields,
            ),
            data_query=DataQuery(
                sql=sql,
                read_only=True,
                limit=limit,
            ),
            data=report_data,
            validation=validation,
        )
