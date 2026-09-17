"""Load and merge Catalog AP pipeline configs (platform + tenant)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.ap_pipeline.defaults import DEFAULT_PIPELINE_KEY, default_platform_config
from app.ap_skills.types import ALL_SKILLS

logger = logging.getLogger("orchestrator.ap_pipeline")

_catalog_ref: Any = None


def set_catalog_store(catalog: Any) -> None:
    global _catalog_ref
    _catalog_ref = catalog


def get_catalog_store() -> Any:
    return _catalog_ref


@dataclass
class ResolvedPipeline:
    """Normalized pipeline knobs for one AP run (omit-skills path)."""

    skills_order: list[str] = field(default_factory=list)
    skills_enabled: Optional[list[str]] = None
    default_connector_id: Optional[str] = None
    default_resource: Optional[str] = None
    thresholds: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    source: str = "code"  # tenant | platform | code


def _json_obj(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _sanitize_skills(order: Any) -> Optional[list[str]]:
    if not isinstance(order, list) or not order:
        return None
    out: list[str] = []
    for item in order:
        sid = str(item or "").strip()
        if not sid:
            continue
        if sid not in ALL_SKILLS:
            logger.warning("ap_pipeline_unknown_skill", extra={"skill_id": sid})
            return None
        out.append(sid)
    return out or None


def _sanitize_enabled(enabled: Any) -> Optional[list[str]]:
    if enabled is None:
        return None
    if not isinstance(enabled, list):
        return None
    out = [str(s).strip() for s in enabled if str(s or "").strip() in ALL_SKILLS]
    return out


def _merge_layer(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge overlay onto base; nested thresholds/flags merge key-wise.

    Null overlay values do not wipe base (treat null as inherit).
    """
    merged = dict(base)
    for key, value in overlay.items():
        if value is None:
            continue
        if key in ("thresholds", "flags") and isinstance(value, dict):
            nested = dict(merged.get(key) or {})
            for nk, nv in value.items():
                if nv is not None:
                    nested[nk] = nv
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _config_from_row(raw: Any, *, source: str) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    cfg = _json_obj(raw)
    if cfg is None:
        logger.warning("ap_pipeline_bad_json", extra={"source": source})
        return None
    order = _sanitize_skills(cfg.get("skills_order"))
    if order is None and "skills_order" in cfg:
        logger.warning("ap_pipeline_bad_skills_order", extra={"source": source})
        return None
    cleaned = dict(cfg)
    if order is not None:
        cleaned["skills_order"] = order
    cleaned["skills_enabled"] = _sanitize_enabled(cfg.get("skills_enabled"))
    return cleaned


def _to_resolved(cfg: dict[str, Any], *, source: str) -> ResolvedPipeline:
    order = _sanitize_skills(cfg.get("skills_order")) or list(
        default_platform_config()["skills_order"]
    )
    connector = cfg.get("default_connector_id")
    resource = cfg.get("default_resource")
    return ResolvedPipeline(
        skills_order=order,
        skills_enabled=_sanitize_enabled(cfg.get("skills_enabled")),
        default_connector_id=str(connector).strip() if connector else None,
        default_resource=str(resource).strip() if resource else None,
        thresholds=dict(cfg.get("thresholds") or {})
        if isinstance(cfg.get("thresholds"), dict)
        else {},
        flags=dict(cfg.get("flags") or {}) if isinstance(cfg.get("flags"), dict) else {},
        source=source,
    )


async def _fetch_config_json(catalog: Any, query: str, *args: Any) -> Any:
    row = await catalog._run("fetchrow", query, *args)
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get("config_json")
    try:
        return row["config_json"]
    except (KeyError, TypeError, IndexError):
        getter = getattr(row, "get", None)
        if callable(getter):
            return getter("config_json")
    return None


async def load_platform_config(catalog: Any) -> Optional[dict[str, Any]]:
    raw = await _fetch_config_json(
        catalog,
        "SELECT config_json FROM platform_ap_pipeline "
        "WHERE pipeline_key = $1 AND is_active = TRUE",
        DEFAULT_PIPELINE_KEY,
    )
    return _config_from_row(raw, source="platform")


async def load_tenant_config(catalog: Any, tenant_id: str) -> Optional[dict[str, Any]]:
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    raw = await _fetch_config_json(
        catalog,
        "SELECT config_json FROM tenant_ap_pipeline "
        "WHERE tenant_id = $1 AND is_active = TRUE",
        tid,
    )
    return _config_from_row(raw, source="tenant")


async def resolve_pipeline_config(tenant_id: str) -> ResolvedPipeline:
    """tenant → platform → code. Bad/missing layers fall through."""
    code = default_platform_config()
    catalog = _catalog_ref
    if catalog is None:
        return _to_resolved(code, source="code")

    platform_cfg: Optional[dict[str, Any]] = None
    tenant_cfg: Optional[dict[str, Any]] = None
    try:
        platform_cfg = await load_platform_config(catalog)
    except Exception as exc:
        logger.warning(
            "ap_pipeline_platform_load_failed",
            extra={"error_type": type(exc).__name__},
        )
    try:
        tenant_cfg = await load_tenant_config(catalog, tenant_id)
    except Exception as exc:
        logger.warning(
            "ap_pipeline_tenant_load_failed",
            extra={"error_type": type(exc).__name__, "tenant_id": tenant_id},
        )

    merged = dict(code)
    source = "code"
    if platform_cfg is not None:
        merged = _merge_layer(merged, platform_cfg)
        source = "platform"
    if tenant_cfg is not None:
        merged = _merge_layer(merged, tenant_cfg)
        source = "tenant"
    return _to_resolved(merged, source=source)


def apply_connector_defaults(document_job: dict[str, Any], pipeline: ResolvedPipeline) -> None:
    """Fill payload connector_id / resource when omitted."""
    if pipeline.default_connector_id and not str(document_job.get("connector_id") or "").strip():
        document_job["connector_id"] = pipeline.default_connector_id
    if pipeline.default_resource and not str(document_job.get("resource") or "").strip():
        document_job["resource"] = pipeline.default_resource


def merge_thresholds(
    *,
    plan_thresholds: Optional[dict[str, Any]],
    pipeline: Optional[ResolvedPipeline],
) -> dict[str, Any]:
    """ap_tenant_plans thresholds, then Catalog pipeline non-null thresholds."""
    out: dict[str, Any] = {}
    if isinstance(plan_thresholds, dict):
        out.update(plan_thresholds)
    if pipeline is not None:
        for key, value in (pipeline.thresholds or {}).items():
            if value is not None:
                out[key] = value
    return out
