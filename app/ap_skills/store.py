"""Postgres persistence for AP runs, artifacts, tenant plans, and credit audit."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

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
