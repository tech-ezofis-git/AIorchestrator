"""Postgres persistence for AP runs, artifacts, tenant plans, and credit audit."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from app.ap_skills.tenant_db import ezfb_items_table
from app.data_import.ident import quote_ident

logger = logging.getLogger("orchestrator.ap_store")


class ApStoreUnavailableError(Exception):
    """Raised when an AP write/read against Postgres fails."""


class DBExecutor(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str)


def _json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


_EZFB_SKIP_COLS = frozenset(
    {
        "item_id",
        "itemid",
        "id",
        "createdat",
        "created_at",
        "createdby",
        "created_by",
        "modifiedat",
        "modified_at",
        "modifiedby",
        "modified_by",
        "isdeleted",
        "is_deleted",
        "todaytask",
        "ismarked",
        "is_marked",
    }
)


def _norm_col(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _map_header_to_ezfb_columns(
    *,
    header: dict[str, Any],
    columns: list[str],
    form_controls: list[dict[str, str]],
    line_items: Optional[list[Any]] = None,
) -> dict[str, Any]:
    by_lower = {c.lower(): c for c in columns}
    by_norm = {_norm_col(c): c for c in columns if _norm_col(c)}
    skip = {c.lower() for c in _EZFB_SKIP_COLS}
    assignments: dict[str, Any] = {}

    def _assign(column: Optional[str], value: Any) -> None:
        if not column or column.lower() in skip or value is None:
            return
        actual = by_lower.get(column.lower()) or by_norm.get(_norm_col(column))
        if actual and actual.lower() not in skip:
            assignments[actual] = value

    for key, value in (header or {}).items():
        if value is None or str(value).strip() == "":
            continue
        _assign(str(key), value)
        if " " in str(key):
            _assign(str(key).replace(" ", "_"), value)

    for control in form_controls or []:
        names = [
            str(control.get("name") or "").strip(),
            str(control.get("column_name") or "").strip(),
            str(control.get("json_id") or "").strip(),
        ]
        value = None
        for name in names:
            if name and header.get(name) not in (None, ""):
                value = header.get(name)
                break
            if name and _norm_col(name) in {_norm_col(k): header.get(k) for k in header}:
                value = next(
                    (header[k] for k in header if _norm_col(k) == _norm_col(name) and header.get(k) not in (None, "")),
                    None,
                )
                if value is not None:
                    break
        if value is None:
            continue
        for name in names:
            _assign(name, value)
        if names[0]:
            _assign(names[0].replace(" ", "_"), value)

    if line_items:
        table_col = next((c for c in columns if "line" in c.lower() and "item" in c.lower()), None)
        if table_col:
            _assign(table_col, json.dumps(line_items, default=str))
    return assignments


class ApStore:
    def __init__(self, db: DBExecutor, *, tenant_pools: Any = None):
        self._fallback = db
        self._tenant_pools = tenant_pools

    async def _db(self, tenant_id: str) -> DBExecutor:
        try:
            if self._tenant_pools is not None:
                return await self._tenant_pools.acquire(tenant_id)
            return self._fallback
        except ApStoreUnavailableError:
            raise
        except Exception as exc:
            logger.warning(
                "ap_tenant_db_acquire_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc

    async def create_run(
        self,
        *,
        session_id: str,
        tenant_id: str,
        item_key: str,
        requested_skills: list[str],
    ) -> str:
        run_id = str(uuid.uuid4())
        try:
            db = await self._db(tenant_id)
            await db.fetchrow(
                "INSERT INTO ap_runs (id, session_id, tenant_id, item_key, requested_skills, status) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6) RETURNING id",
                run_id,
                session_id,
                tenant_id,
                item_key,
                _json_dump(requested_skills),
                "running",
            )
        except Exception as exc:
            logger.warning(
                "ap_run_insert_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc
        return run_id

    async def finish_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        status: str,
        decision: Optional[str],
        credits_charged: int,
    ) -> None:
        try:
            db = await self._db(tenant_id)
            await db.execute(
                "UPDATE ap_runs SET status = $2, decision = $3, credits_charged = $4, "
                "finished_at = now() WHERE id = $1",
                run_id,
                status,
                decision,
                credits_charged,
            )
        except Exception as exc:
            logger.warning(
                "ap_run_update_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc

    async def save_artifact(
        self,
        *,
        run_id: str,
        tenant_id: str,
        item_key: str,
        skill_id: str,
        result: dict[str, Any],
    ) -> None:
        try:
            db = await self._db(tenant_id)
            await db.execute(
                "INSERT INTO ap_skill_artifacts (run_id, tenant_id, item_key, skill_id, result_json) "
                "VALUES ($1, $2, $3, $4, $5::jsonb)",
                run_id,
                tenant_id,
                item_key,
                skill_id,
                _json_dump(result),
            )
        except Exception as exc:
            logger.warning(
                "ap_artifact_insert_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc

    async def load_artifacts(self, *, tenant_id: str, item_key: str) -> dict[str, dict[str, Any]]:
        try:
            db = await self._db(tenant_id)
            rows = await db.fetch(
                "SELECT skill_id, result_json, created_at FROM ap_skill_artifacts "
                "WHERE tenant_id = $1 AND item_key = $2",
                tenant_id,
                item_key,
            )
        except Exception as exc:
            logger.warning(
                "ap_artifact_read_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc

        latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
        for row in rows:
            skill_id = str(_row_get(row, "skill_id"))
            created = _row_get(row, "created_at") or datetime.min.replace(tzinfo=timezone.utc)
            payload = _json_load(_row_get(row, "result_json")) or {}
            if not isinstance(payload, dict):
                continue
            prev = latest.get(skill_id)
            if prev is None or created >= prev[0]:
                latest[skill_id] = (created, payload)
        return {skill_id: data for skill_id, (_, data) in latest.items()}

    async def list_skill_artifacts(
        self, *, tenant_id: str, skill_id: str
    ) -> list[dict[str, Any]]:
        try:
            db = await self._db(tenant_id)
            rows = await db.fetch(
                "SELECT item_key, result_json FROM ap_skill_artifacts "
                "WHERE tenant_id = $1 AND skill_id = $2",
                tenant_id,
                skill_id,
            )
        except Exception as exc:
            logger.warning("ap_artifact_list_failed", extra={"error_type": type(exc).__name__})
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(_row_get(row, "result_json")) or {}
            if isinstance(payload, dict):
                out.append({"item_key": _row_get(row, "item_key"), **payload})
        return out

    async def get_plan(self, tenant_id: str) -> Optional[dict[str, Any]]:
        try:
            db = await self._db(tenant_id)
            row = await db.fetchrow(
                "SELECT enabled_skills, thresholds FROM ap_tenant_plans WHERE tenant_id = $1",
                tenant_id,
            )
        except Exception as exc:
            logger.warning(
                "ap_plan_read_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None
        if row is None:
            return None
        enabled = _json_load(_row_get(row, "enabled_skills")) or []
        thresholds = _json_load(_row_get(row, "thresholds")) or {}
        if not isinstance(enabled, list):
            enabled = []
        if not isinstance(thresholds, dict):
            thresholds = {}
        return {
            "tenant_id": tenant_id,
            "enabled_skills": [str(s) for s in enabled],
            "thresholds": thresholds,
        }

    async def fetch_form_controls(self, *, tenant_id: str, form_id: Optional[str]) -> list[dict[str, str]]:
        """Load wFormControl name/columnName/jsonId for AP metadata key aliases."""
        fid = str(form_id or "").strip()
        if not fid:
            return []
        queries = (
            (
                'SELECT "name" AS name, "columnName" AS column_name, "jsonId" AS json_id '
                'FROM dbo.wformcontrol WHERE lower(CAST("wFormId" AS text)) = lower($1) '
                'AND COALESCE("isDeleted", 0) = 0',
                (fid,),
            ),
            (
                "SELECT name, columnname AS column_name, jsonid AS json_id "
                "FROM wformcontrol WHERE lower(wformid::text) = lower($1) "
                "AND COALESCE(isdeleted, 0) = 0",
                (fid,),
            ),
        )
        try:
            db = await self._db(tenant_id)
        except Exception as exc:
            logger.warning(
                "ap_form_controls_db_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return []
        for sql, params in queries:
            try:
                rows = await db.fetch(sql, *params)
            except Exception as exc:
                logger.warning(
                    "ap_form_controls_lookup_failed",
                    extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
                )
                continue
            out: list[dict[str, str]] = []
            for row in rows or []:
                name = str(_row_get(row, "name") or "").strip()
                column_name = str(_row_get(row, "column_name") or "").strip()
                json_id = str(_row_get(row, "json_id") or "").strip()
                if name or column_name or json_id:
                    out.append({"name": name, "column_name": column_name, "json_id": json_id})
            if out:
                return out
        return []

    async def apply_ezfb_item_fields(
        self,
        *,
        tenant_id: str,
        form_id: str,
        form_entry_id: int,
        header: dict[str, Any],
        line_items: Optional[list[Any]] = None,
        form_controls: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """UPDATE ezfb_{token}_items for the ticket formEntryId. Does not insert a new row."""
        table = ezfb_items_table(form_id)
        if not table or not header:
            return {"ok": False, "updated": 0, "reason": "missing_table_or_header", "table": table}
        try:
            db = await self._db(tenant_id)
            loc = await db.fetchrow(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE lower(table_name) = lower($1)
                  AND table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_schema
                LIMIT 1
                """,
                table,
            )
            if loc is None:
                return {"ok": False, "updated": 0, "reason": "table_not_found", "table": table}
            schema = str(_row_get(loc, "table_schema") or "dbo")
            real_table = str(_row_get(loc, "table_name") or table)
            col_rows = await db.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                real_table,
            )
            columns = [str(_row_get(row, "column_name")) for row in col_rows or []]
            assignments = _map_header_to_ezfb_columns(
                header=header,
                columns=columns,
                form_controls=form_controls or [],
                line_items=line_items,
            )
            if not assignments:
                return {
                    "ok": False,
                    "updated": 0,
                    "reason": "no_column_match",
                    "table": real_table,
                    "columns": columns[:40],
                }
            pk = next(
                (name for name in ("item_id", "itemid", "id") if name.lower() in {c.lower() for c in columns}),
                None,
            )
            if pk is None:
                return {"ok": False, "updated": 0, "reason": "no_pk", "table": real_table}
            pk_actual = next(c for c in columns if c.lower() == pk.lower())
            sets = []
            args: list[Any] = []
            for index, (col, value) in enumerate(assignments.items(), start=1):
                sets.append(f"{quote_ident(col)} = ${index}")
                args.append(value)
            args.append(int(form_entry_id))
            sql = (
                f"UPDATE {quote_ident(schema)}.{quote_ident(real_table)} "
                f"SET {', '.join(sets)} "
                f"WHERE {quote_ident(pk_actual)} = ${len(args)}"
            )
            await db.execute(sql, *args)
            logger.info(
                "ap_ezfb_item_updated",
                extra={
                    "table": real_table,
                    "form_entry_id": form_entry_id,
                    "columns": sorted(assignments.keys()),
                },
            )
            return {
                "ok": True,
                "updated": 1,
                "table": real_table,
                "columns": sorted(assignments.keys()),
                "form_entry_id": form_entry_id,
            }
        except Exception as exc:
            logger.warning(
                "ap_ezfb_item_update_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200], "table": table},
            )
            return {"ok": False, "updated": 0, "reason": type(exc).__name__, "table": table}

    async def fetch_workflow_activity_id(
        self,
        *,
        tenant_id: str,
        workflow_id: Optional[str] = None,
        step_name: str = "AP AGENT 1",
    ) -> Optional[str]:
        """Resolve ActivityId from workflow.WorkflowSteps (Postgres port of apagentv6)."""
        name = (step_name or "AP AGENT 1").strip() or "AP AGENT 1"
        wf = (workflow_id or "").strip() or None
        queries = (
            (
                'SELECT "ActivityId" AS activity_id FROM workflow."WorkflowSteps" '
                'WHERE "Name" = $1 AND ($2::text IS NULL OR CAST("WorkflowId" AS text) = $2) '
                'ORDER BY "Order" LIMIT 1',
                (name, wf),
            ),
            (
                "SELECT activityid AS activity_id FROM workflow.workflowsteps "
                "WHERE name = $1 AND ($2::text IS NULL OR workflowid::text = $2) "
                'ORDER BY "order" LIMIT 1',
                (name, wf),
            ),
        )
        try:
            db = await self._db(tenant_id)
        except Exception as exc:
            logger.warning(
                "ap_activityid_db_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None
        for sql, params in queries:
            try:
                row = await db.fetchrow(sql, *params)
            except Exception as exc:
                logger.warning(
                    "ap_activityid_lookup_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                        "step_name": name,
                    },
                )
                continue
            if row is None:
                continue
            value = _row_get(row, "activity_id") or _row_get(row, "ActivityId") or _row_get(row, "activityid")
            text = str(value).strip() if value not in (None, "") else ""
            if text:
                return text
        return None

    async def record_credit(
        self,
        *,
        run_id: str,
        tenant_id: str,
        skill_id: str,
        credits: int,
        identify: str,
        status: str,
    ) -> None:
        try:
            db = await self._db(tenant_id)
            await db.execute(
                "INSERT INTO ap_credit_ledger (run_id, tenant_id, skill_id, credits, identify, status) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                run_id,
                tenant_id,
                skill_id,
                credits,
                identify,
                status,
            )
        except Exception as exc:
            logger.warning(
                "ap_credit_ledger_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise ApStoreUnavailableError("AP store is currently unavailable.") from exc
