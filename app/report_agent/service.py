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
    ReportCatalogItem,
    ReportColumnOut,
    ReportSpec,
    ReportTypeInfo,
    RunReportRequest,
    RunReportResponse,
    SaveReportRequest,
    SchemaTableSlice,
    ScopeOption,
    UpdateReportRequest,
)
from app.report_agent.library_store import ReportLibraryStore
from app.report_agent.data_service import execute_report_query
from app.report_agent.definition_lock import (
    fallback_definition_from_prompt,
    lock_report_definition,
    merge_spec_into_definition,
    parse_definition_json,
)
from app.report_agent.definition_sql import generate_definition_sql
from app.report_agent.form_schema import extract_form_fields, extract_repository_fields
from app.report_agent.metadata_service import (
    DatabaseSchema,
    WorkflowMeta,
    get_database_schema,
    load_ezofis_catalogs,
    resolve_report_source,
    schema_from_form_fields,
    schema_from_repository_fields,
    schema_for_all_repositories,
    schema_for_all_workflows,
    RepositoryMeta,
)
from app.report_agent.pack import report_system_prompt
from app.report_agent.report_spec import (
    enrich_spec_with_schema,
    fallback_report_spec,
    normalize_report_spec,
    parse_report_spec_json,
    report_spec_to_prompt,
)
from app.report_agent.report_types import (
    get_report_type,
    list_report_types,
    validate_report_type_input,
)
from app.report_agent.schema_scope import (
    is_catalog_table,
    preferred_fact_table,
    resolve_scope_options,
    schema_slice_to_prompt_block,
    scope_schema,
)
from app.report_agent.sql_validator import validate_read_only_sql

logger = logging.getLogger("orchestrator.report_agent")

_FALLBACK_PROMPT_SYSTEM = (
    "You are the EZOFIS Report Agent Phase 1. Return ONLY one valid reportSpec JSON object "
    "for the client short prompt UI. Never invent schema. Never write SQL."
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
        library_store: Any = None,
        ezofis_client: Any = None,
    ):
        self._tenant_pools = tenant_pools
        self._db_pool = db_pool
        self._catalog_store = catalog_store
        self._llm = llm_adapter
        self._settings = settings
        self._library = library_store or ReportLibraryStore(db_pool)
        self._ezofis = ezofis_client

    def list_types(self) -> list[ReportTypeInfo]:
        return list_report_types()

    def list_catalog(self) -> list[ReportCatalogItem]:
        from app.report_agent.report_catalog import list_catalog

        return list_catalog()

    async def ensure_library_schema(self) -> None:
        await self._library.ensure_schema()

    async def list_saved_reports(
        self,
        *,
        tenant_id: str = "",
        status: Optional[str] = None,
        domain: Optional[str] = None,
        q: Optional[str] = None,
        favorite_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._library.list_reports(
            tenant_id=tenant_id or "",
            status=status,
            domain=domain,
            q=q,
            favorite_only=favorite_only,
        )

    async def get_saved_report(self, report_id: str, *, tenant_id: str = "") -> Optional[dict[str, Any]]:
        return await self._library.get_report(report_id, tenant_id=tenant_id or "")

    async def save_report(self, request: SaveReportRequest) -> dict[str, Any]:
        return await self._library.save_report(request.model_dump(by_alias=True, exclude_none=True))

    async def update_saved_report(
        self,
        report_id: str,
        request: UpdateReportRequest,
        *,
        tenant_id: str = "",
    ) -> Optional[dict[str, Any]]:
        payload = request.model_dump(by_alias=True, exclude_none=True)
        return await self._library.update_report(report_id, payload, tenant_id=tenant_id or "")

    async def delete_saved_report(self, report_id: str, *, tenant_id: str = "") -> bool:
        return await self._library.delete_report(report_id, tenant_id=tenant_id or "")

    async def run_saved_report(
        self,
        report_id: str,
        *,
        tenant_id: str = "",
        page: int = 1,
        page_size: int = 25,
        model: Optional[str] = None,
    ) -> RunReportResponse:
        saved = await self._library.get_report(report_id, tenant_id=tenant_id or "")
        if saved is None:
            raise ValueError("Saved report not found.")
        spec = saved.get("reportSpec") or {}
        run = await self.run_report(
            RunReportRequest(
                report_type=saved.get("reportType") or "all_workflows",
                workflow_name=saved.get("workflowName"),
                repository_name=saved.get("repositoryName"),
                tenant_id=tenant_id or saved.get("tenantId") or "",
                report_prompt=saved.get("reportPrompt") or "",
                report_spec=ReportSpec.model_validate(spec) if spec else None,
                page=page,
                page_size=page_size,
                model=model,
            )
        )
        await self._library.touch_run(report_id, tenant_id=tenant_id or "")
        return run

    async def _resolve_db(self, tenant_id: Optional[str], *, allow_fallback: bool = True) -> Any:
        tid = (tenant_id or "").strip()
        if tid and self._tenant_pools is not None:
            try:
                fn = getattr(self._tenant_pools, "acquire_for_global_search", None)
                if callable(fn):
                    return await fn(tid, self._catalog_store)
                return await self._tenant_pools.acquire(tid)
            except Exception as exc:
                logger.warning(
                    "report_agent_tenant_db_fallback",
                    extra={
                        "tenant_id": tid[:12],
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                        "allow_fallback": allow_fallback,
                    },
                )
                if not allow_fallback:
                    raise RuntimeError(
                        "Unable to access the tenant database. Workflow and repository lists "
                        "cannot be loaded from the local database."
                    ) from exc
                if self._db_pool is None:
                    raise RuntimeError(
                        "Unable to access tenant database and no local database pool is configured."
                    ) from exc
                return self._db_pool
        return self._db_pool

    async def _resolve_llm_presets(
        self,
        tenant_id: Optional[str],
        model: Optional[str] = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return (primary_overrides, fallback override dicts) for chat_completion."""
        if model:
            return {"model": model}, []
        tid = (tenant_id or "").strip()
        if not tid or self._catalog_store is None:
            return {}, []
        resolved = await apply_tenant_agent_llm(self._catalog_store, tid, "report")
        primary: dict[str, Any] = {}
        preset = resolved.get("overrides")
        if isinstance(preset, dict):
            primary.update({k: v for k, v in preset.items() if v is not None})
        fallbacks = list(resolved.get("fallback_chain") or [])
        fallback = resolved.get("fallback_overrides")
        if not fallbacks and isinstance(fallback, dict) and fallback:
            fallbacks = [fallback]
        if primary:
            fallbacks = [item for item in fallbacks if item.get("model") != primary.get("model")]
        return primary, fallbacks

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
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        primary, fallbacks = await self._resolve_llm_presets(tenant_id, model)
        if isinstance(fallbacks, dict):
            fallbacks = [fallbacks]
        elif not isinstance(fallbacks, list):
            fallbacks = []
        try:
            result = await self._llm.chat_completion(messages, **primary)
        except LLMAdapterError as primary_exc:
            if not fallbacks:
                raise RuntimeError(f"Report Agent model call failed: {primary_exc}") from primary_exc
            last_exc: LLMAdapterError = primary_exc
            for fallback in fallbacks:
                logger.warning(
                    "report_agent_llm_primary_failed_retrying_fallback",
                    extra={
                        "error": str(last_exc)[:200],
                        "fallback_model": fallback.get("model"),
                    },
                )
                try:
                    result = await self._llm.chat_completion(messages, **fallback)
                    last_exc = None  # type: ignore[assignment]
                    break
                except LLMAdapterError as fallback_exc:
                    last_exc = fallback_exc
            if last_exc is not None:
                raise RuntimeError(
                    f"Report Agent model call failed (primary and fallback): {last_exc}"
                ) from last_exc
        return str(result.get("content") or ""), result.get("usage")

    async def _list_scope_from_ezofis(
        self,
        *,
        scope: str,
        tenant_id: Optional[str],
    ) -> list[ScopeOption]:
        tid = (tenant_id or "").strip()
        if not tid or self._ezofis is None:
            return []
        try:
            if scope in ("workflows", "workflow"):
                rows = await self._ezofis.list_workflows(tid)
            else:
                rows = await self._ezofis.list_repositories(tid)
        except Exception as exc:
            logger.warning(
                "report_agent_ezofis_scope_failed",
                extra={"scope": scope, "error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return []
        options: list[ScopeOption] = []
        for row in rows or []:
            name = str((row or {}).get("name") or "").strip()
            if not name:
                continue
            options.append(ScopeOption(id=str((row or {}).get("id") or name), name=name))
        return options

    @staticmethod
    def _overlay_physical_columns(
        api_schema: DatabaseSchema,
        db_schema: Optional[DatabaseSchema],
    ) -> DatabaseSchema:
        if db_schema is None or not api_schema.tables:
            return api_schema
        from app.report_agent.metadata_service import schema_from_columns

        columns = []
        for schema_name, table in api_schema.tables:
            live = db_schema.resolve_table(schema_name, table)
            if live:
                physical = db_schema.get_columns_for_table(*live)
                if physical:
                    columns.extend(physical)
                    continue
            columns.extend(api_schema.get_columns_for_table(schema_name, table))
        if not columns:
            return api_schema
        return schema_from_columns(
            columns,
            workflows=list(api_schema.workflows or db_schema.workflows or []),
            repositories=list(api_schema.repositories or db_schema.repositories or []),
        )

    async def _schema_from_ezofis(
        self,
        *,
        report_type: str,
        tenant_id: Optional[str],
        workflow_name: Optional[str],
        repository_name: Optional[str],
    ) -> Optional[DatabaseSchema]:
        tid = (tenant_id or "").strip()
        if not tid or self._ezofis is None:
            return None
        if report_type == "all_workflows":
            return await self._schema_from_ezofis_all_workflows(tid)
        if report_type == "all_repositories":
            return await self._schema_from_ezofis_all_repositories(tid)
        if report_type == "specific_repository":
            return await self._schema_from_ezofis_repository(tid, repository_name)
        if report_type != "specific_workflow":
            return None
        want = (workflow_name or "").strip()
        if not want:
            return None
        try:
            listed = await self._ezofis.list_workflows(tid)
            match = None
            for row in listed or []:
                name = str((row or {}).get("name") or "").strip()
                wid = str((row or {}).get("id") or "").strip()
                if name.lower() == want.lower() or wid.lower() == want.lower():
                    match = row
                    break
            if match is None:
                for row in listed or []:
                    name = str((row or {}).get("name") or "").strip()
                    if want.lower() in name.lower() or name.lower() in want.lower():
                        match = row
                        break
            if match is None:
                return None
            wf_id = str(match.get("id") or "").strip()
            wf_name = str(match.get("name") or want).strip()
            detail = await self._ezofis.get_workflow(tenant_id=tid, workflow_id=wf_id)
            form_id = ""
            repo_id = ""
            if isinstance(detail, dict):
                form_id = str(detail.get("formId") or detail.get("FormId") or "").strip()
                repo_id = str(detail.get("repositoryId") or detail.get("RepositoryId") or "").strip()
                if not form_id:
                    wj = detail.get("workflowJson") or {}
                    if isinstance(wj, str):
                        try:
                            wj = json.loads(wj)
                        except json.JSONDecodeError:
                            wj = {}
                    if isinstance(wj, dict):
                        general = ((wj.get("settings") or {}).get("general") or {}) if isinstance(wj.get("settings"), dict) else {}
                        initiate = general.get("initiateUsing") or {}
                        if isinstance(initiate, dict):
                            form_id = str(initiate.get("formId") or "").strip()
                            repo_id = repo_id or str(initiate.get("repositoryId") or "").strip()
            if not form_id:
                return None
            form = await self._ezofis.get_form(tenant_id=tid, form_id=form_id)
            form_json = (form or {}).get("formJson") if isinstance(form, dict) else None
            fields = extract_form_fields(form_json)
            if not fields:
                logger.warning("report_agent_ezofis_form_empty", extra={"workflow": wf_name})
                return None
            workflow = WorkflowMeta(
                workflow_id=wf_id,
                name=wf_name,
                form_id=form_id,
                repository_id=repo_id or None,
            )
            return schema_from_form_fields(form_id=form_id, fields=fields, workflow=workflow)
        except Exception as exc:
            logger.warning(
                "report_agent_ezofis_form_schema_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None

    async def _schema_from_ezofis_repository(
        self,
        tenant_id: str,
        repository_name: Optional[str],
    ) -> Optional[DatabaseSchema]:
        want = (repository_name or "").strip()
        if not want:
            return None
        try:
            listed = await self._ezofis.list_repositories(tenant_id)
            match = None
            for row in listed or []:
                name = str((row or {}).get("name") or "").strip()
                rid = str((row or {}).get("id") or "").strip()
                if name.lower() == want.lower() or rid.lower() == want.lower():
                    match = row
                    break
            if match is None:
                for row in listed or []:
                    name = str((row or {}).get("name") or "").strip()
                    if want.lower() in name.lower() or name.lower() in want.lower():
                        match = row
                        break
            if match is None:
                return None
            repo_id = str(match.get("id") or "").strip()
            repo_name = str(match.get("name") or want).strip()
            detail = await self._ezofis.get_repository(tenant_id=tenant_id, repository_id=repo_id)
            if not isinstance(detail, dict) or not detail.get("ok", True):
                return None
            fields = extract_repository_fields(detail.get("fields"))
            if not fields:
                logger.warning("report_agent_ezofis_repo_empty", extra={"repository": repo_name})
                return None
            return schema_from_repository_fields(
                repository_id=repo_id,
                name=repo_name,
                items_table=str(detail.get("itemsTableName") or detail.get("ItemsTableName") or "").strip() or None,
                fields=fields,
            )
        except Exception as exc:
            logger.warning(
                "report_agent_ezofis_repo_schema_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None

    async def _schema_from_ezofis_all_workflows(self, tenant_id: str) -> Optional[DatabaseSchema]:
        try:
            listed = await self._ezofis.list_workflows(tenant_id)
        except Exception as exc:
            logger.warning(
                "report_agent_ezofis_all_workflows_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None
        workflows = [
            WorkflowMeta(
                workflow_id=str((row or {}).get("id") or "").strip(),
                name=str((row or {}).get("name") or "").strip(),
            )
            for row in listed or []
            if str((row or {}).get("name") or "").strip()
        ]
        if not workflows:
            return None
        return schema_for_all_workflows(workflows)

    async def _schema_from_ezofis_all_repositories(self, tenant_id: str) -> Optional[DatabaseSchema]:
        try:
            listed = await self._ezofis.list_repositories(tenant_id)
        except Exception as exc:
            logger.warning(
                "report_agent_ezofis_all_repositories_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None
        repositories = [
            RepositoryMeta(
                repository_id=str((row or {}).get("id") or "").strip(),
                name=str((row or {}).get("name") or "").strip(),
            )
            for row in listed or []
            if str((row or {}).get("name") or "").strip()
        ]
        if not repositories:
            return None
        return schema_for_all_repositories(repositories)

    async def list_scope_options(
        self,
        *,
        scope: str,
        tenant_id: Optional[str],
    ) -> list[ScopeOption]:
        scope_l = (scope or "").strip().lower()
        if scope_l not in ("workflows", "workflow", "repositories", "repository"):
            raise ValueError("scope must be 'workflows' or 'repositories'.")
        from_api = await self._list_scope_from_ezofis(scope=scope_l, tenant_id=tenant_id)
        if from_api:
            return from_api
        try:
            db = await self._resolve_db(tenant_id, allow_fallback=True)
        except Exception as exc:
            logger.warning(
                "report_agent_scope_db_unavailable",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return []
        if db is None:
            return []
        try:
            workflows, repositories = await load_ezofis_catalogs(db)
        except Exception as exc:
            logger.warning(
                "report_agent_scope_catalog_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            workflows, repositories = [], []
        if scope_l in ("workflows", "workflow") and workflows:
            return [ScopeOption(id=wf.workflow_id or wf.name, name=wf.name) for wf in workflows]
        if scope_l in ("repositories", "repository") and repositories:
            return [
                ScopeOption(id=repo.repository_id or repo.name, name=repo.name)
                for repo in repositories
            ]
        try:
            schema = await get_database_schema(db)
            return resolve_scope_options(schema, scope)
        except Exception as exc:
            logger.warning(
                "report_agent_scope_schema_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return []

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
        source_warnings: list[str] = []
        schema = None
        if db is not None:
            try:
                schema, source_warnings = await resolve_report_source(
                    db,
                    report_type=rt.key,
                    workflow_name=request.workflow_name,
                    repository_name=request.repository_name,
                )
            except Exception as exc:
                logger.warning(
                    "report_agent_source_resolve_failed",
                    extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
                )
                source_warnings.append(f"Live source resolve failed ({exc}); scanned information_schema.")
        api_schema = await self._schema_from_ezofis(
            report_type=rt.key,
            tenant_id=request.tenant_id,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
        )
        if api_schema and api_schema.tables:
            schema = self._overlay_physical_columns(api_schema, schema)
            if rt.key == "all_workflows":
                names = [wf.name for wf in schema.workflows if wf.name]
                source_warnings.append(
                    f"All Workflows is scoped to {len(names)} tenant workflows"
                    + (f": {', '.join(names[:12])}." if names else ".")
                )
            elif rt.key == "all_repositories":
                names = [repo.name for repo in schema.repositories if repo.name]
                source_warnings.append(
                    f"All Repositories is scoped to {len(names)} tenant repositories"
                    + (f": {', '.join(names[:12])}." if names else ".")
                )
            else:
                label = (request.workflow_name or request.repository_name or "").strip() or "selected source"
                source_warnings.append(
                    f"Columns taken from '{label}' in EZOFIS, not from another workflow or repository."
                )
        elif rt.key in ("specific_workflow", "specific_repository"):
            if schema is None or not schema.tables:
                schema = DatabaseSchema()
                source_warnings.append(
                    "Could not load this workflow/repository form. Not using another workflow's columns."
                )
        elif schema is None or not schema.tables:
            if db is None:
                raise RuntimeError("Unable to access database metadata: No active database pool.")
            schema = await get_database_schema(db)
        tables = scope_schema(
            schema,
            rt,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
        )
        if not tables and schema.tables:
            from app.models.report_agent import SchemaColumnSlice, SchemaTableSlice as _Slice

            tables = [
                _Slice(
                    schema_name=s,
                    table=t,
                    columns=[
                        SchemaColumnSlice(name=c.column, type=c.data_type)
                        for c in schema.get_columns_for_table(s, t)
                    ],
                )
                for s, t in schema.tables
            ]
        schema_block = schema_slice_to_prompt_block(tables)
        preferred = preferred_fact_table(tables)
        from app.report_agent.report_spec import available_columns_from_tables as _avail_cols_prompt

        live_columns = _avail_cols_prompt(tables, limit=120)
        system = await report_system_prompt(
            "prompt",
            tenant_id=request.tenant_id,
            settings=self._settings,
            fallback=_FALLBACK_PROMPT_SYSTEM,
        )
        catalog_names = [wf.name for wf in (schema.workflows or []) if wf.name]
        if rt.key in ("all_repositories", "specific_repository"):
            catalog_names = [repo.name for repo in (schema.repositories or []) if repo.name]
        user_payload = {
            "reportType": rt.key,
            "description": (request.description or "").strip(),
            "workflowName": (request.workflow_name or "").strip() or None,
            "repositoryName": (request.repository_name or "").strip() or None,
            "catalogNames": catalog_names or None,
            "preferredSource": (
                {"schemaName": preferred.schema_name, "table": preferred.table}
                if preferred
                else None
            ),
        }
        from app.report_agent.report_catalog import catalog_prompt_hint

        user = (
            "Build a client-facing short reportSpec JSON for the Report Builder UI.\n"
            "Return ONLY one valid JSON object with keys: title, objective, reportType, "
            "columns[{key,label,formula?}], filters[{field,op,value}], groupBy, sort{field,direction}, "
            "sourceSchema, sourceTable, availableColumns, warnings.\n"
            "No markdown fences, no commentary, no SQL.\n"
            "Use only columns from the approved schema slice. Set sourceSchema/sourceTable to the "
            "preferredSource fact table (ezfb_*_items or items_*), never Workflows/Repositories/wForm.\n"
            "availableColumns MUST be the live column list below. Put the columns that match the "
            "user description first; do not invent names that are not in that list.\n"
            "Do not invent status literals; use not_null when the stored value is unknown. "
            "Prefer formulas that use only live columns (SUM, COUNT, CASE). "
            "Never add Invoice_No, PO_Number, or other fields that are not in the live column list.\n"
            "Different descriptions must produce different columns, filters, and groupBy.\n\n"
            f"{catalog_prompt_hint(request.description or '', workflow_name=request.workflow_name, repository_name=request.repository_name, report_type=rt.key)}\n\n"
            f"Input:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"Live columns from tenant data: {json.dumps(live_columns)}\n\n"
            f"{schema_block}\n"
        )
        warnings: list[str] = list(source_warnings)
        if (request.tenant_id or "").strip() and db is self._db_pool:
            warnings.append(
                "Tenant database is unavailable; generated this prompt from the local/default database."
            )
        if not tables:
            warnings.append("No schema tables matched this report type/scope.")
        elif not live_columns:
            warnings.append("Fact table was found but it has no discoverable columns.")

        spec: Optional[ReportSpec] = None
        try:
            content, _usage = await self._chat(
                system=system,
                user=user,
                tenant_id=request.tenant_id,
                model=request.model,
            )
            try:
                spec = parse_report_spec_json(content)
            except ValueError:
                # Model may still return long prompt text — wrap as objective
                text = (content or "").strip()
                if text.startswith("```"):
                    text = text.strip("`")
                    if text.lower().startswith("text"):
                        text = text[4:].lstrip()
                if text:
                    spec = normalize_report_spec(
                        {"title": (request.description or "").strip() or rt.name, "objective": text[:500]},
                        report_type=rt.key,
                        description=request.description or "",
                    )
                    warnings.append("Model returned text; converted into short prompt fields.")
        except Exception as exc:
            logger.warning("report_agent_prompt_llm_failed", extra={"error": str(exc)[:200]})
            warnings.append(f"Prompt model failed ({exc}); used schema-backed short prompt.")

        if spec is None:
            spec = fallback_report_spec(
                description=request.description or "",
                report_type=rt.key,
                tables=tables,
                warnings=warnings,
            )
        else:
            if not spec.report_type:
                spec = spec.model_copy(update={"report_type": rt.key})
            if not spec.columns:
                fb = fallback_report_spec(
                    description=request.description or "",
                    report_type=rt.key,
                    tables=tables,
                )
                spec = spec.model_copy(
                    update={
                        "title": spec.title or fb.title,
                        "objective": spec.objective or fb.objective,
                        "columns": fb.columns,
                        "filters": spec.filters or fb.filters,
                        "group_by": spec.group_by or fb.group_by,
                        "sort": spec.sort or fb.sort,
                        "available_columns": fb.available_columns,
                    }
                )
            spec = enrich_spec_with_schema(spec, schema, tables)
            for w in warnings:
                if w not in spec.warnings:
                    spec.warnings.append(w)

        from app.report_agent.report_catalog import align_spec_to_intent
        from app.report_agent.report_spec import available_columns_from_tables as _avail_cols

        spec = align_spec_to_intent(
            spec,
            description=request.description or "",
            report_type=rt.key,
            available_columns=spec.available_columns or _avail_cols(tables),
            source_schema=spec.source_schema,
            source_table=spec.source_table,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
        )
        spec = enrich_spec_with_schema(spec, schema, tables)

        if preferred and (not spec.source_table or is_catalog_table(spec.source_table)):
            spec = spec.model_copy(
                update={"source_schema": preferred.schema_name, "source_table": preferred.table}
            )

        if not spec.available_columns:
            from app.report_agent.report_spec import available_columns_from_tables

            spec = spec.model_copy(
                update={"available_columns": available_columns_from_tables(tables)}
            )

        prompt_text = report_spec_to_prompt(
            spec,
            workflow_name=request.workflow_name,
            repository_name=request.repository_name,
        )
        if not prompt_text.strip():
            raise RuntimeError("Failed to build report prompt from reportSpec.")
        title = (spec.title or request.description or "").strip() or rt.name
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "report_agent_prompt_generated",
            extra={
                "report_type": rt.key,
                "tables": len(tables),
                "columns": len(spec.columns),
                "duration_ms": duration_ms,
            },
        )
        return GeneratePromptResponse(
            report_type=rt.key,
            title=title,
            report_prompt=prompt_text,
            report_spec=spec,
            warnings=list(spec.warnings or []),
            schema_tables=[
                f"{t.schema_name + '.' if t.schema_name else ''}{t.table}" for t in tables
            ],
            duration_ms=duration_ms,
        )

    def _align_report_spec(
        self,
        spec: ReportSpec,
        *,
        description: str,
        report_type: str,
        workflow_name: Optional[str] = None,
        repository_name: Optional[str] = None,
    ) -> ReportSpec:
        from app.report_agent.report_catalog import align_spec_to_intent

        return align_spec_to_intent(
            spec,
            description=description or spec.title or "",
            report_type=report_type or spec.report_type or "",
            available_columns=spec.available_columns or [c.key for c in spec.columns],
            source_schema=spec.source_schema,
            source_table=spec.source_table,
            workflow_name=workflow_name,
            repository_name=repository_name,
        )

    async def _run_virtual_catalog(
        self,
        request: RunReportRequest,
        spec: ReportSpec,
        report_type: Optional[str],
        t0: float,
    ) -> Optional[RunReportResponse]:
        from app.report_agent.catalog_execute import execute_catalog_spec, is_virtual_catalog_table

        if not is_virtual_catalog_table(spec.source_table):
            return None
        if self._ezofis is None:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return RunReportResponse(
                title=spec.title or "Report",
                report_type=report_type or spec.report_type,
                columns=[ReportColumnOut(key=c.key, label=c.label or c.key) for c in spec.columns],
                rows=[],
                total_count=0,
                page=request.page,
                page_size=request.page_size,
                filters=[],
                summary={},
                warnings=["EZOFIS catalog is not configured; cannot count live workflow/repository rows."],
                duration_ms=duration_ms,
            )
        try:
            result = await execute_catalog_spec(
                self._ezofis,
                spec,
                tenant_id=request.tenant_id or "",
                report_type=report_type or spec.report_type or "",
                page=request.page,
                page_size=request.page_size,
            )
        except Exception as exc:
            logger.warning(
                "report_agent_catalog_execute_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None
        if result is None:
            return None
        columns, rows, total, warnings = result
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        return RunReportResponse(
            title=spec.title or "Report",
            report_type=report_type or spec.report_type,
            columns=columns,
            rows=rows,
            total_count=total,
            page=request.page,
            page_size=request.page_size,
            filters=[],
            summary={},
            warnings=list(spec.warnings or []) + list(warnings or []),
            duration_ms=duration_ms,
        )

    async def run_report(self, request: RunReportRequest) -> RunReportResponse:
        t0 = time.perf_counter()
        prompt = (request.report_prompt or "").strip()
        spec = request.report_spec
        if spec is not None and not isinstance(spec, ReportSpec):
            spec = normalize_report_spec(spec)
        if not prompt and spec is not None:
            prompt = report_spec_to_prompt(
                spec,
                workflow_name=request.workflow_name,
                repository_name=request.repository_name,
            )
        if not prompt:
            raise ValueError("reportPrompt or reportSpec is required.")

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

        if spec is not None:
            report_type_key = rt.key if rt else (request.report_type or spec.report_type or "")
            spec = self._align_report_spec(
                spec,
                description=spec.title or prompt,
                report_type=report_type_key,
                workflow_name=request.workflow_name,
                repository_name=request.repository_name,
            )
            if not spec.source_table and report_type_key == "all_workflows":
                spec = spec.model_copy(
                    update={"source_schema": spec.source_schema or "workflow", "source_table": "workflow_instances_all"}
                )
            elif not spec.source_table and report_type_key == "all_repositories":
                spec = spec.model_copy(
                    update={"source_schema": spec.source_schema or "repository", "source_table": "items_all"}
                )
            catalog_resp = await self._run_virtual_catalog(request, spec, report_type_key, t0)
            if catalog_resp is not None:
                return catalog_resp

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
            tables = deduped[:18]

        schema_block = schema_slice_to_prompt_block(tables)
        preferred = preferred_fact_table(tables)
        if spec is not None and spec.source_table:
            resolved = schema.resolve_table(spec.source_schema, spec.source_table)
            if resolved:
                preferred = SchemaTableSlice(
                    schema_name=resolved[0],
                    table=resolved[1],
                    columns=preferred.columns if preferred else [],
                )
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
            "preferredSource": (
                {"schemaName": preferred.schema_name, "table": preferred.table}
                if preferred
                else None
            ),
        }
        user = (
            "Interpret the report prompt into locked reportDefinition JSON.\n"
            "Return ONLY one valid JSON object. No markdown fences, no commentary, "
            "no trailing commas, no undefined values.\n"
            "sources[0] must be preferredSource (the fact table with report rows). "
            "Never use Workflows, Repositories, or wForm as the row source.\n"
            "Do not invent status equality values; use not_null when unknown.\n\n"
            f"Input:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"{schema_block}\n"
        )

        warnings: list[str] = []
        if (request.tenant_id or "").strip() and db is self._db_pool:
            warnings.append(
                "Tenant database is unavailable; running this report from the local/default database."
            )
        raw_def: Optional[dict[str, Any]] = None
        try:
            content, _usage = await self._chat(
                system=system,
                user=user,
                tenant_id=request.tenant_id,
                model=request.model,
            )
            try:
                raw_def = parse_definition_json(content)
            except ValueError as parse_exc:
                logger.warning(
                    "report_agent_definition_parse_retry",
                    extra={"error": str(parse_exc)[:200], "snippet": str(content)[:240]},
                )
                repair_user = (
                    "Your previous response was invalid JSON. "
                    "Return ONLY a corrected reportDefinition JSON object. "
                    "No markdown, no trailing commas.\n\n"
                    f"Broken response:\n{str(content)[:4000]}\n\n"
                    f"Original request:\n{json.dumps(user_payload, indent=2)}\n\n"
                    f"{schema_block}\n"
                )
                repaired, _ = await self._chat(
                    system=system,
                    user=repair_user,
                    tenant_id=request.tenant_id,
                    model=request.model,
                )
                raw_def = parse_definition_json(repaired)
        except Exception as exc:
            logger.warning("report_agent_run_llm_failed", extra={"error": str(exc)[:200]})
            raw_def = fallback_definition_from_prompt(
                prompt,
                schema,
                report_type=rt.key if rt else request.report_type,
                title=(request.report_type or "Report"),
                preferred_schema=preferred.schema_name if preferred else None,
                preferred_table=preferred.table if preferred else None,
            )
            if raw_def is None:
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
            warnings.append(
                f"Model JSON failed ({exc}); used schema-backed fallback definition."
            )

        if raw_def is None:
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
                warnings=["Model failed to produce a report definition."],
                duration_ms=duration_ms,
            )

        if spec is not None:
            raw_def = merge_spec_into_definition(raw_def, spec)
        locked = lock_report_definition(
            raw_def,
            schema,
            report_type=rt.key if rt else request.report_type,
            preferred_schema=preferred.schema_name if preferred else None,
            preferred_table=preferred.table if preferred else None,
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
