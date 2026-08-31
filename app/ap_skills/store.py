"""Postgres persistence for AP runs, artifacts, tenant plans, and credit audit."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from app.ap_skills.tenant_db import ezfb_items_table, repository_items_table
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


_REPO_SKIP_COLS = _EZFB_SKIP_COLS | {
    "filename",
    "filepath",
    "file_path",
    "file_name",
    "contenttype",
    "content_type",
    "filesize",
    "size",
    "mimetype",
    "mime_type",
    "repositoryid",
    "repository_id",
    "parentid",
    "parent_id",
    "blobpath",
    "blob_path",
    "version",
    "versionno",
    "islatest",
    "is_latest",
    "extension",
    "fileextension",
}


def _norm_col(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def guid_compact(value: str) -> str:
    """Hyphens/braces stripped so UUID text matches compact blob ids."""
    return "".join(ch for ch in str(value or "") if ch.isalnum()).lower()


def _is_guid_column_type(data_type: str, udt_name: str = "") -> bool:
    blob = f"{data_type} {udt_name}".lower()
    if "int" in blob and "uuid" not in blob:
        return False
    return any(token in blob for token in ("uuid", "text", "char", "name"))


def pick_repository_item_pk(
    columns: list[str],
    types: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Prefer a GUID/text item key. Do not match a hyphenated UUID against integer id."""
    types = types or {}
    lower_map = {c.lower(): c for c in columns}
    type_lookup = {str(k).lower(): str(v or "") for k, v in types.items()}
    for candidate in ("itemid", "item_id", "id"):
        actual = lower_map.get(candidate)
        if not actual:
            continue
        col_type = type_lookup.get(actual.lower()) or ""
        if col_type and not _is_guid_column_type(col_type):
            continue
        return actual
    return None


def _map_header_to_ezfb_columns(
    *,
    header: dict[str, Any],
    columns: list[str],
    form_controls: list[dict[str, str]],
    line_items: Optional[list[Any]] = None,
    skip_columns: Optional[set[str]] = None,
) -> dict[str, Any]:
    by_lower = {c.lower(): c for c in columns}
    by_norm = {_norm_col(c): c for c in columns if _norm_col(c)}
    skip = {c.lower() for c in (skip_columns or _EZFB_SKIP_COLS)}
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


def _row_as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(k): row[k] for k in keys()}
    return {}


def _ci_get(data: dict[str, Any], *names: str) -> Any:
    if not data:
        return None
    lower = {str(k).lower().replace("_", ""): v for k, v in data.items()}
    for name in names:
        key = str(name).lower().replace("_", "")
        if key in lower and lower[key] not in (None, ""):
            return lower[key]
    return None


def _execute_rowcount(status: Any) -> Optional[int]:
    if not isinstance(status, str):
        return None
    parts = status.replace("\t", " ").split()
    if not parts:
        return None
    try:
        return int(parts[-1])
    except (TypeError, ValueError):
        return None


def _row_mostly_empty(row: dict[str, Any]) -> bool:
    skip_norm = {_norm_col(c) for c in _EZFB_SKIP_COLS} | {"itemid", "id"}
    filled = 0
    for key, value in row.items():
        if _norm_col(key) in skip_norm:
            continue
        if value in (None, "", b""):
            continue
        filled += 1
        if filled >= 2:
            return False
    return True


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

    async def fetch_repository_fields(
        self, *, tenant_id: str, repository_id: Optional[str]
    ) -> list[dict[str, str]]:
        """Load repository field name/columnName/jsonId for items_* column aliases."""
        rid = str(repository_id or "").strip()
        if not rid:
            return []
        queries = (
            (
                'SELECT "name" AS name, "columnName" AS column_name, "jsonId" AS json_id '
                'FROM dbo.wrepositoryfield WHERE lower(CAST("wRepositoryId" AS text)) = lower($1) '
                'AND COALESCE("isDeleted", 0) = 0',
                (rid,),
            ),
            (
                "SELECT name, columnname AS column_name, jsonid AS json_id "
                "FROM dbo.wrepositoryfield WHERE lower(wrepositoryid::text) = lower($1) "
                "AND COALESCE(isdeleted, 0) = 0",
                (rid,),
            ),
            (
                'SELECT "Name" AS name, "ColumnName" AS column_name, "JsonId" AS json_id '
                'FROM repository."Fields" WHERE lower(CAST("RepositoryId" AS text)) = lower($1)',
                (rid,),
            ),
        )
        try:
            db = await self._db(tenant_id)
        except Exception as exc:
            logger.warning(
                "ap_repo_fields_db_failed",
                extra={"error_type": type(exc).__name__},
            )
            return []
        for sql, params in queries:
            try:
                rows = await db.fetch(sql, *params)
            except Exception:
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

    def _ticket_from_row(self, row: Any) -> dict[str, Any]:
        data = _row_as_dict(row)
        item = _ci_get(data, "itemid", "repositoryitemid", "item_id", "id")
        entry = _ci_get(data, "formentryid", "form_entry_id")
        out: dict[str, Any] = {}
        workflow_id = _ci_get(data, "workflowid", "workflow_id")
        instance_id = _ci_get(data, "instanceid", "instance_id")
        repository_id = _ci_get(data, "repositoryid", "repository_id")
        form_id = _ci_get(data, "formid", "form_id", "wformid")
        if workflow_id:
            out["workflow_id"] = str(workflow_id).strip()
        if instance_id:
            out["instance_id"] = str(instance_id).strip()
        if repository_id:
            out["repository_id"] = str(repository_id).strip()
        if form_id:
            out["form_id"] = str(form_id).strip()
        text_item = str(item or "").strip()
        if text_item and len(text_item) == 36 and text_item.count("-") == 4:
            out["item_id"] = text_item
        if entry not in (None, ""):
            try:
                out["form_entry_id"] = int(str(entry).strip())
            except (TypeError, ValueError):
                if str(entry).strip().isdigit():
                    out["form_entry_id"] = int(str(entry).strip())
        return out

    async def fetch_ticket_context(
        self,
        *,
        tenant_id: str,
        instance_id: Optional[str] = None,
        repository_item_id: Optional[str] = None,
        form_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Best-effort lookup of formId / formEntryId / repository item from the tenant DB."""
        inst = str(instance_id or "").strip()
        repo_item = str(repository_item_id or "").strip()
        fid = str(form_id or "").strip()
        if not inst and not repo_item:
            return {}
        try:
            db = await self._db(tenant_id)
        except Exception as exc:
            logger.warning("ap_ticket_context_db_failed", extra={"error_type": type(exc).__name__})
            return {}

        queries: list[tuple[str, tuple[Any, ...]]] = []
        if inst:
            queries.extend(
                [
                    (
                        'SELECT * FROM workflow."WorkflowInstances" '
                        'WHERE CAST("Id" AS text) = $1 LIMIT 1',
                        (inst,),
                    ),
                    (
                        "SELECT * FROM workflow.workflowinstances "
                        "WHERE id::text = $1 LIMIT 1",
                        (inst,),
                    ),
                    (
                        'SELECT * FROM dbo."WorkflowInstances" '
                        'WHERE CAST("Id" AS text) = $1 LIMIT 1',
                        (inst,),
                    ),
                ]
            )
        if repo_item:
            queries.extend(
                [
                    (
                        'SELECT * FROM dbo."RepositoryItem" WHERE CAST("Id" AS text) = $1 LIMIT 1',
                        (repo_item,),
                    ),
                    (
                        "SELECT * FROM dbo.repositoryitem WHERE id::text = $1 LIMIT 1",
                        (repo_item,),
                    ),
                    (
                        'SELECT * FROM dbo."RepositoryItems" WHERE CAST("Id" AS text) = $1 LIMIT 1',
                        (repo_item,),
                    ),
                ]
            )
        merged: dict[str, Any] = {}
        for sql, params in queries:
            try:
                row = await db.fetchrow(sql, *params)
            except Exception:
                continue
            if row is None:
                continue
            found = self._ticket_from_row(row)
            for key, value in found.items():
                if value not in (None, "") and merged.get(key) in (None, ""):
                    merged[key] = value
            if merged.get("form_id") and merged.get("form_entry_id") is not None:
                break

        if (not merged.get("form_id") or merged.get("form_entry_id") is None) and inst:
            try:
                col_rows = await db.fetch(
                    """
                    SELECT table_schema, table_name, column_name
                    FROM information_schema.columns
                    WHERE lower(column_name) IN (
                        'formentryid', 'form_entry_id', 'formid', 'form_id',
                        'instanceid', 'instance_id'
                    )
                    AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    """
                )
            except Exception:
                col_rows = []
            tables: dict[tuple[str, str], set[str]] = {}
            for row in col_rows or []:
                schema = str(_row_get(row, "table_schema") or "")
                name = str(_row_get(row, "table_name") or "")
                col = str(_row_get(row, "column_name") or "").lower()
                if schema and name:
                    tables.setdefault((schema, name), set()).add(col)
            for (schema, name), cols in list(tables.items())[:12]:
                has_entry = bool(cols & {"formentryid", "form_entry_id"})
                has_inst = bool(cols & {"instanceid", "instance_id", "id"})
                if not has_entry:
                    continue
                inst_col = "instanceid" if "instanceid" in cols else (
                    "instance_id" if "instance_id" in cols else "id"
                )
                try:
                    row = await db.fetchrow(
                        f"SELECT * FROM {quote_ident(schema)}.{quote_ident(name)} "
                        f"WHERE CAST({quote_ident(inst_col)} AS text) = $1 LIMIT 1",
                        inst,
                    )
                except Exception:
                    continue
                if row is None:
                    continue
                found = self._ticket_from_row(row)
                for key, value in found.items():
                    if value not in (None, "") and merged.get(key) in (None, ""):
                        merged[key] = value
                if merged.get("form_entry_id") is not None:
                    break
        if fid and not merged.get("form_id"):
            merged["form_id"] = fid
        if merged:
            logger.info("ap_ticket_context_resolved", extra={k: merged.get(k) for k in (
                "form_id", "form_entry_id", "item_id", "repository_id"
            )})
        return merged

    async def _locate_ezfb_table(
        self,
        db: Any,
        *,
        form_id: str,
        form_entry_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        guessed = ezfb_items_table(form_id)
        candidates: list[tuple[str, str]] = []
        if guessed:
            try:
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
                    guessed,
                )
            except Exception:
                loc = None
            if loc is not None:
                return {
                    "schema": str(_row_get(loc, "table_schema") or "dbo"),
                    "table": str(_row_get(loc, "table_name") or guessed),
                }
        try:
            rows = await db.fetch(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND lower(table_name) LIKE 'ezfb_%_items'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_schema
                """
            )
        except Exception:
            rows = []
        token = (guessed or "").lower()
        for row in rows or []:
            schema = str(_row_get(row, "table_schema") or "dbo")
            name = str(_row_get(row, "table_name") or "")
            if not name:
                continue
            if token and name.lower() == token:
                candidates.insert(0, (schema, name))
            else:
                candidates.append((schema, name))
        if form_entry_id is None:
            return (
                {"schema": candidates[0][0], "table": candidates[0][1]}
                if len(candidates) == 1
                else None
            )
        for schema, name in candidates:
            try:
                col_rows = await db.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    """,
                    schema,
                    name,
                )
                columns = [str(_row_get(r, "column_name")) for r in col_rows or []]
                pk = next(
                    (c for c in columns if c.lower() in {"item_id", "itemid", "id"}),
                    None,
                )
                if pk is None:
                    continue
                hit = await db.fetchrow(
                    f"SELECT 1 AS ok FROM {quote_ident(schema)}.{quote_ident(name)} "
                    f"WHERE {quote_ident(pk)} = $1 LIMIT 1",
                    int(form_entry_id),
                )
            except Exception:
                continue
            if hit is not None:
                return {"schema": schema, "table": name}
        return None

    async def latest_empty_ezfb_item(self, *, tenant_id: str, form_id: str) -> Optional[int]:
        """Return the newest blank ezfb row for this form (the ticket the workflow just created)."""
        fid = str(form_id or "").strip()
        if not fid:
            return None
        try:
            db = await self._db(tenant_id)
            loc = await self._locate_ezfb_table(db, form_id=fid)
            if loc is None:
                return None
            schema, real_table = loc["schema"], loc["table"]
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
            pk = next((c for c in columns if c.lower() in {"item_id", "itemid", "id"}), None)
            if pk is None:
                return None
            row = await db.fetchrow(
                f"SELECT * FROM {quote_ident(schema)}.{quote_ident(real_table)} "
                f"ORDER BY {quote_ident(pk)} DESC LIMIT 1"
            )
            if row is None:
                return None
            data = _row_as_dict(row)
            if not _row_mostly_empty(data):
                return None
            pk_val = _ci_get(data, pk, "item_id", "itemid", "id")
            return int(pk_val) if pk_val is not None else None
        except Exception as exc:
            logger.warning(
                "ap_ezfb_latest_empty_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None

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
        if not header:
            return {"ok": False, "updated": 0, "reason": "missing_table_or_header", "table": table}
        try:
            db = await self._db(tenant_id)
            loc = await self._locate_ezfb_table(
                db, form_id=form_id, form_entry_id=int(form_entry_id)
            )
            if loc is None:
                return {"ok": False, "updated": 0, "reason": "table_not_found", "table": table}
            schema = loc["schema"]
            real_table = loc["table"]
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
                args.append(value if isinstance(value, str) else (
                    json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
                ))
            args.append(int(form_entry_id))
            sql = (
                f"UPDATE {quote_ident(schema)}.{quote_ident(real_table)} "
                f"SET {', '.join(sets)} "
                f"WHERE {quote_ident(pk_actual)} = ${len(args)}"
            )
            status = await db.execute(sql, *args)
            updated = _execute_rowcount(status)
            if updated == 0:
                logger.warning(
                    "ap_ezfb_item_update_zero_rows",
                    extra={"table": real_table, "form_entry_id": form_entry_id},
                )
                return {
                    "ok": False,
                    "updated": 0,
                    "reason": "row_not_found",
                    "table": real_table,
                    "form_entry_id": form_entry_id,
                }
            logger.info(
                "ap_ezfb_item_updated",
                extra={
                    "table": real_table,
                    "form_entry_id": form_entry_id,
                    "columns": sorted(assignments.keys()),
                    "updated": updated if updated is not None else 1,
                },
            )
            return {
                "ok": True,
                "updated": updated if updated is not None else 1,
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

    async def _locate_table_by_name(self, db: Any, table_name: str) -> Optional[dict[str, Any]]:
        try:
            loc = await db.fetchrow(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE lower(table_name) = lower($1)
                  AND table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY CASE
                    WHEN lower(table_schema) = 'repository' THEN 0
                    WHEN table_schema = 'dbo' THEN 1
                    ELSE 2
                END, table_schema
                LIMIT 1
                """,
                table_name,
            )
        except Exception:
            return None
        if loc is None:
            return None
        return {
            "schema": str(_row_get(loc, "table_schema") or "repository"),
            "table": str(_row_get(loc, "table_name") or table_name),
        }

    async def latest_empty_repository_item(
        self, *, tenant_id: str, repository_id: str
    ) -> Optional[str]:
        """Newest blank row in repository.items_{token} (the ticket item just created)."""
        table = repository_items_table(repository_id)
        if not table:
            return None
        try:
            db = await self._db(tenant_id)
            loc = await self._locate_table_by_name(db, table)
            if loc is None:
                return None
            schema, real_table = loc["schema"], loc["table"]
            col_rows = await db.fetch(
                """
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                real_table,
            )
            columns = [str(_row_get(row, "column_name")) for row in col_rows or []]
            types = {
                str(_row_get(row, "column_name")): str(
                    _row_get(row, "data_type") or _row_get(row, "udt_name") or ""
                )
                for row in col_rows or []
            }
            pk = pick_repository_item_pk(columns, types)
            if pk is None:
                return None
            order_col = next(
                (c for c in columns if c.lower() in {"createdat", "created_at", pk.lower()}),
                pk,
            )
            order_actual = next(c for c in columns if c.lower() == order_col.lower())
            row = await db.fetchrow(
                f"SELECT * FROM {quote_ident(schema)}.{quote_ident(real_table)} "
                f"ORDER BY {quote_ident(order_actual)} DESC LIMIT 1"
            )
            if row is None:
                return None
            data = _row_as_dict(row)
            if not _row_mostly_empty(data):
                return None
            pk_val = _ci_get(data, pk, "id", "itemid", "item_id")
            text = str(pk_val).strip() if pk_val not in (None, "") else ""
            return text or None
        except Exception as exc:
            logger.warning(
                "ap_repo_latest_empty_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return None

    async def apply_repository_item_fields(
        self,
        *,
        tenant_id: str,
        repository_id: str,
        item_id: str,
        header: dict[str, Any],
        line_items: Optional[list[Any]] = None,
        form_controls: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """UPDATE repository.items_{token} for the ticket item GUID. Does not insert."""
        table = repository_items_table(repository_id)
        item_guid = str(item_id or "").strip()
        if not table or not header or not item_guid:
            return {
                "ok": False,
                "updated": 0,
                "reason": "missing_table_or_header",
                "table": table,
            }
        try:
            db = await self._db(tenant_id)
            loc = await self._locate_table_by_name(db, table)
            if loc is None:
                return {"ok": False, "updated": 0, "reason": "table_not_found", "table": table}
            schema = loc["schema"]
            real_table = loc["table"]
            col_rows = await db.fetch(
                """
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                real_table,
            )
            columns = [str(_row_get(row, "column_name")) for row in col_rows or []]
            types = {
                str(_row_get(row, "column_name")): str(
                    _row_get(row, "data_type") or _row_get(row, "udt_name") or ""
                )
                for row in col_rows or []
            }
            repo_fields = await self.fetch_repository_fields(
                tenant_id=tenant_id, repository_id=repository_id
            )
            controls = list(form_controls or []) + repo_fields
            assignments = _map_header_to_ezfb_columns(
                header=header,
                columns=columns,
                form_controls=controls,
                line_items=line_items,
                skip_columns=_REPO_SKIP_COLS,
            )
            if not assignments:
                return {
                    "ok": False,
                    "updated": 0,
                    "reason": "no_column_match",
                    "table": f"{schema}.{real_table}",
                    "columns": columns[:40],
                }
            pk_actual = pick_repository_item_pk(columns, types)
            if pk_actual is None:
                return {"ok": False, "updated": 0, "reason": "no_pk", "table": real_table}
            sets = []
            args: list[Any] = []
            for index, (col, value) in enumerate(assignments.items(), start=1):
                sets.append(f"{quote_ident(col)} = ${index}")
                args.append(
                    value
                    if isinstance(value, str)
                    else (
                        json.dumps(value, default=str)
                        if isinstance(value, (dict, list))
                        else str(value)
                    )
                )
            compact = guid_compact(item_guid)
            if len(compact) != 32:
                return {
                    "ok": False,
                    "updated": 0,
                    "reason": "invalid_item_guid",
                    "table": f"{schema}.{real_table}",
                    "item_id": item_guid,
                }
            args.append(compact)
            sql = (
                f"UPDATE {quote_ident(schema)}.{quote_ident(real_table)} "
                f"SET {', '.join(sets)} "
                f"WHERE lower(regexp_replace(CAST({quote_ident(pk_actual)} AS text), "
                f"'[^0-9a-fA-F]', '', 'g')) = ${len(args)}"
            )
            status = await db.execute(sql, *args)
            updated = _execute_rowcount(status)
            if updated == 0:
                logger.warning(
                    "ap_repo_item_update_zero_rows",
                    extra={"table": real_table, "item_id": item_guid},
                )
                return {
                    "ok": False,
                    "updated": 0,
                    "reason": "row_not_found",
                    "table": f"{schema}.{real_table}",
                    "item_id": item_guid,
                }
            logger.info(
                "ap_repo_item_updated",
                extra={
                    "table": f"{schema}.{real_table}",
                    "item_id": item_guid,
                    "columns": sorted(assignments.keys()),
                    "updated": updated if updated is not None else 1,
                },
            )
            return {
                "ok": True,
                "updated": updated if updated is not None else 1,
                "table": f"{schema}.{real_table}",
                "columns": sorted(assignments.keys()),
                "item_id": item_guid,
            }
        except Exception as exc:
            logger.warning(
                "ap_repo_item_update_failed",
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
