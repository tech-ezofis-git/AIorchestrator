"""Catalog read/write helpers for AP pipeline console + audit."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from app.ap_pipeline.defaults import DEFAULT_PIPELINE_KEY, default_platform_config
from app.ap_pipeline.resolve import (
    _config_from_row,
    _fetch_config_json,
    get_catalog_store,
)
from app.ap_skills.types import ALL_SKILL_ORDER, ALL_SKILLS

logger = logging.getLogger("orchestrator.ap_pipeline")


class ApPipelineStoreError(ValueError):
    """Invalid pipeline config from console — safe as HTTP 400."""


def validate_pipeline_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate a console-submitted config_json."""
    if not isinstance(raw, dict):
        raise ApPipelineStoreError("config must be an object.")
    order = raw.get("skills_order")
    if not isinstance(order, list) or not order:
        raise ApPipelineStoreError("skills_order must be a non-empty list.")
    cleaned_order: list[str] = []
    for item in order:
        sid = str(item or "").strip()
        if not sid:
            continue
        if sid not in ALL_SKILLS:
            raise ApPipelineStoreError(f"Unknown skill: {sid}")
        if sid not in cleaned_order:
            cleaned_order.append(sid)
    if not cleaned_order:
        raise ApPipelineStoreError("skills_order must include at least one known skill.")

    enabled_raw = raw.get("skills_enabled")
    enabled: Optional[list[str]]
    if enabled_raw is None:
        enabled = None
    elif isinstance(enabled_raw, list):
        enabled = [str(s).strip() for s in enabled_raw if str(s or "").strip() in ALL_SKILLS]
        if not enabled:
            raise ApPipelineStoreError("skills_enabled cannot be empty when provided.")
    else:
        raise ApPipelineStoreError("skills_enabled must be a list or null.")

    thresholds = raw.get("thresholds") if isinstance(raw.get("thresholds"), dict) else {}
    flags = raw.get("flags") if isinstance(raw.get("flags"), dict) else {}
    connector = raw.get("default_connector_id")
    resource = raw.get("default_resource")

    def _opt_num(value: Any) -> Any:
        if value is None or value == "":
            return None
        try:
            return float(value) if not isinstance(value, bool) else None
        except (TypeError, ValueError) as exc:
            raise ApPipelineStoreError(f"Invalid numeric threshold: {value!r}") from exc

    out = {
        "skills_order": cleaned_order,
        "skills_enabled": enabled,
        "default_connector_id": (str(connector).strip() or None) if connector is not None else None,
        "default_resource": (str(resource).strip() or None) if resource is not None else None,
        "thresholds": {
            "approved": int(_opt_num(thresholds.get("approved")))
            if _opt_num(thresholds.get("approved")) is not None
            else None,
            "partial": int(_opt_num(thresholds.get("partial")))
            if _opt_num(thresholds.get("partial")) is not None
            else None,
            "amount_tolerance": _opt_num(thresholds.get("amount_tolerance")),
        },
        "flags": {
            "use_planner": bool(flags.get("use_planner", False)),
            "force_hana_po_lookup": bool(flags.get("force_hana_po_lookup", True)),
        },
    }
    return out


async def fetch_platform_row(catalog: Any) -> dict[str, Any]:
    raw = await _fetch_config_json(
        catalog,
        "SELECT config_json FROM platform_ap_pipeline "
        "WHERE pipeline_key = $1 AND is_active = TRUE",
        DEFAULT_PIPELINE_KEY,
    )
    cfg = _config_from_row(raw, source="platform") or default_platform_config()
    return {
        "pipeline_key": DEFAULT_PIPELINE_KEY,
        "config": cfg,
        "source": "platform" if raw is not None else "code",
    }


async def fetch_tenant_row(catalog: Any, tenant_id: str) -> Optional[dict[str, Any]]:
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    row = await catalog._run(
        "fetchrow",
        "SELECT id, config_json, version, is_active, updated_by, updated_at "
        "FROM tenant_ap_pipeline WHERE tenant_id = $1",
        tid,
    )
    if row is None:
        return None

    def _get(key: str) -> Any:
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except Exception:
            getter = getattr(row, "get", None)
            return getter(key) if callable(getter) else None

    cfg = _config_from_row(_get("config_json"), source="tenant")
    if cfg is None:
        return None
    return {
        "id": str(_get("id") or ""),
        "tenant_id": tid,
        "config": cfg,
        "version": int(_get("version") or 1),
        "is_active": bool(_get("is_active") if _get("is_active") is not None else True),
        "updated_by": _get("updated_by"),
        "updated_at": str(_get("updated_at") or ""),
    }


async def upsert_tenant_pipeline(
    catalog: Any,
    *,
    tenant_id: str,
    config: dict[str, Any],
    changed_by: str = "console",
) -> dict[str, Any]:
    tid = (tenant_id or "").strip()
    if not tid:
        raise ApPipelineStoreError("tenant_id is required.")
    cleaned = validate_pipeline_config(config)
    existing = await fetch_tenant_row(catalog, tid)
    old_json = json.dumps((existing or {}).get("config") or {}, default=str)
    new_json = json.dumps(cleaned, default=str)
    row_id = uuid.uuid4() if existing is None else existing.get("id") or uuid.uuid4()
    try:
        row_uuid = uuid.UUID(str(row_id))
    except (TypeError, ValueError):
        row_uuid = uuid.uuid4()

    await catalog._run(
        "execute",
        "INSERT INTO tenant_ap_pipeline "
        "(id, tenant_id, config_json, version, is_active, updated_by) "
        "VALUES ($1, $2, $3::jsonb, 1, TRUE, $4) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "config_json = EXCLUDED.config_json, "
        "version = tenant_ap_pipeline.version + 1, "
        "is_active = TRUE, "
        "updated_by = EXCLUDED.updated_by, "
        "updated_at = now()",
        row_uuid,
        tid,
        new_json,
        (changed_by or "console").strip() or "console",
    )
    action = "CREATE" if existing is None else "UPDATE"
    await catalog._run(
        "execute",
        "INSERT INTO tenant_ap_pipeline_logs "
        "(tenant_id, pipeline_id, action, old_value, new_value, changed_by) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        tid,
        row_uuid,
        action,
        old_json,
        new_json,
        (changed_by or "console").strip() or "console",
    )
    logger.info(
        "ap_pipeline_tenant_upserted",
        extra={"tenant_id": tid, "action": action},
    )
    refreshed = await fetch_tenant_row(catalog, tid)
    return refreshed or {
        "id": str(row_uuid),
        "tenant_id": tid,
        "config": cleaned,
        "version": 1,
        "is_active": True,
        "updated_by": changed_by,
        "updated_at": "",
    }


async def list_tenant_pipeline_logs(
    catalog: Any, *, tenant_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    tid = (tenant_id or "").strip()
    if not tid:
        return []
    rows = await catalog._run(
        "fetch",
        "SELECT id, action, old_value, new_value, changed_by, changed_at "
        "FROM tenant_ap_pipeline_logs WHERE tenant_id = $1 "
        "ORDER BY changed_at DESC LIMIT $2",
        tid,
        max(1, min(int(limit or 20), 100)),
    )
    out: list[dict[str, Any]] = []
    for row in rows or []:
        def _get(key: str, r: Any = row) -> Any:
            if isinstance(r, dict):
                return r.get(key)
            try:
                return r[key]
            except Exception:
                getter = getattr(r, "get", None)
                return getter(key) if callable(getter) else None

        out.append(
            {
                "id": _get("id"),
                "action": _get("action"),
                "old_value": _get("old_value"),
                "new_value": _get("new_value"),
                "changed_by": _get("changed_by"),
                "changed_at": str(_get("changed_at") or ""),
            }
        )
    return out


def known_skills_payload() -> dict[str, Any]:
    return {
        "all_skills": list(ALL_SKILL_ORDER),
        "default_order": list(default_platform_config()["skills_order"]),
    }


def require_catalog() -> Any:
    catalog = get_catalog_store()
    if catalog is None:
        raise ApPipelineStoreError("Catalog store is not available.")
    return catalog
