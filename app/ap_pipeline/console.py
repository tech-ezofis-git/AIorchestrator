"""Console HTTP helpers for AP Pipeline Configuration."""
from __future__ import annotations

from typing import Any, Optional

from app.ap_pipeline import store as pipeline_store
from app.config import Settings


async def console_get(
    *,
    tenant_id: Optional[str],
    settings: Settings,
) -> dict[str, Any]:
    catalog = pipeline_store.require_catalog()
    platform = await pipeline_store.fetch_platform_row(catalog)
    tid = (tenant_id or "").strip() or None
    tenant = await pipeline_store.fetch_tenant_row(catalog, tid) if tid else None
    logs = (
        await pipeline_store.list_tenant_pipeline_logs(catalog, tenant_id=tid)
        if tid
        else []
    )
    return {
        "ap_pipeline_from_db": bool(getattr(settings, "ap_pipeline_from_db", False)),
        "known_skills": pipeline_store.known_skills_payload(),
        "platform": platform,
        "tenant": tenant,
        "logs": logs,
    }


async def console_put_tenant(
    *,
    tenant_id: str,
    config: dict[str, Any],
    changed_by: str = "console",
) -> dict[str, Any]:
    catalog = pipeline_store.require_catalog()
    row = await pipeline_store.upsert_tenant_pipeline(
        catalog,
        tenant_id=tenant_id,
        config=config,
        changed_by=changed_by,
    )
    return {"tenant": row}
