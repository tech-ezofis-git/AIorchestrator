"""Route AP store queries to the per-tenant Postgres database.

App Settings `DATABASE_URL` is the *main* DB (host, user, password, ssl).
AP tables live in `ezofis_Tenant_{first 8 hex chars of tenant_id}` on that
same server — e.g. tenant `2e3b7b37-38a3-4f94-878e-a006dad93230` →
`ezofis_Tenant_2e3b7b37`. Non-UUID tenants (local tests) stay on the main DB.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from app.catalog.url import catalog_pool_kwargs, normalize_catalog_url

logger = logging.getLogger("orchestrator.ap_store")

_HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")
DEFAULT_TENANT_DB_PREFIX = "ezofis_Tenant_"

# Same DDL as db/migrations/0004_create_ap_tables.sql — applied on first
# connect so tenant DBs (and cloud volumes that skipped init scripts) work.
_AP_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS ap_runs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        item_key TEXT NOT NULL,
        requested_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
        status TEXT NOT NULL DEFAULT 'running',
        decision TEXT,
        credits_charged INT NOT NULL DEFAULT 0,
        data_quality JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ap_runs_tenant_item_idx
        ON ap_runs (tenant_id, item_key, created_at DESC)
    """,
    # Code-review finding #2: at most one "running" ap_runs row per
    # (tenant_id, item_key) at a time — a second concurrent create_run()
    # for the same item fails this INSERT instead of silently double
    # processing it (ApStore.create_run catches the conflict). Added here
    # (not just db/migrations/0007_...sql) because ap_runs lives per-tenant
    # in ezofis_Tenant_* and this function is what actually provisions/
    # upgrades those databases.
    """
    ALTER TABLE ap_runs ADD COLUMN IF NOT EXISTS data_quality JSONB
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ap_runs_tenant_item_active_idx
        ON ap_runs (tenant_id, item_key) WHERE status = 'running'
    """,
    """
    CREATE TABLE IF NOT EXISTS ap_skill_artifacts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id UUID REFERENCES ap_runs(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        item_key TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        result_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ap_skill_artifacts_item_skill_idx
        ON ap_skill_artifacts (tenant_id, item_key, skill_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ap_tenant_plans (
        tenant_id TEXT PRIMARY KEY,
        enabled_skills JSONB NOT NULL,
        thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ap_credit_ledger (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id UUID REFERENCES ap_runs(id) ON DELETE SET NULL,
        tenant_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        credits INT NOT NULL DEFAULT 1,
        identify TEXT,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ap_credit_ledger_run_idx
        ON ap_credit_ledger (run_id, created_at DESC)
    """,
)


async def ensure_ap_schema(pool: Any) -> None:
    """Create AP audit/run tables if missing (idempotent)."""
    for stmt in _AP_SCHEMA_SQL:
        await pool.execute(stmt)


def ezfb_items_table(form_id: Optional[str]) -> Optional[str]:
    """Map payload formid to ezfb_{token}_items (numeric id or first 8 of GUID)."""
    raw = str(form_id or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return f"ezfb_{int(raw)}_items"
    compact = raw.replace("-", "").lower()
    if len(compact) >= 8 and all(c in "0123456789abcdef" for c in compact[:8]):
        return f"ezfb_{compact[:8]}_items"
    return None


def repository_items_table(repository_id: Optional[str]) -> Optional[str]:
    """Map repositoryId GUID to items_{first 8 hex} (e.g. items_38b1b6dd)."""
    raw = str(repository_id or "").strip()
    if not raw:
        return None
    compact = raw.replace("-", "").lower()
    if len(compact) >= 8 and all(c in "0123456789abcdef" for c in compact[:8]):
        return f"items_{compact[:8]}"
    return None


CreatePool = Callable[..., Awaitable[Any]]


def tenant_database_name(
    tenant_id: str,
    *,
    prefix: str = DEFAULT_TENANT_DB_PREFIX,
) -> Optional[str]:
    """Return tenant DB name, or None to keep the main DATABASE_URL database."""
    prefix = (prefix or "").strip()
    if not prefix:
        return None
    head = (tenant_id or "").strip().split("-", 1)[0]
    if not _HEX8.fullmatch(head):
        return None
    return f"{prefix}{head.lower()}"


def replace_database_name(database_url: str, database: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


class ApTenantDbPools:
    """Cached asyncpg pools keyed by tenant database name."""

    def __init__(
        self,
        database_url: str,
        *,
        fallback_pool: Any,
        create_pool: CreatePool,
        prefix: str = DEFAULT_TENANT_DB_PREFIX,
        min_size: int = 0,
        max_size: int = 5,
    ):
        self._database_url = database_url
        self._fallback_pool = fallback_pool
        self._create_pool = create_pool
        self._prefix = prefix
        self._min_size = min_size
        self._max_size = max_size
        self._pools: dict[str, Any] = {}
        self._schema_ready: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire(self, tenant_id: str) -> Any:
        db_name = tenant_database_name(tenant_id, prefix=self._prefix)
        if db_name is None:
            if hasattr(self._fallback_pool, "execute"):
                await self._ensure_schema(self._fallback_pool, "__fallback__")
            return self._fallback_pool
        cached = self._pools.get(db_name)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._pools.get(db_name)
            if cached is not None:
                return cached
            raw_url = replace_database_name(self._database_url, db_name)
            url = normalize_catalog_url(raw_url)
            ssl_kwargs = catalog_pool_kwargs(raw_url)
            try:
                pool = await self._create_pool(
                    url,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    **ssl_kwargs,
                )
                await self._ensure_schema(pool, db_name)
            except Exception as exc:
                logger.warning(
                    "ap_tenant_db_connect_failed",
                    extra={
                        "database": db_name,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                    },
                )
                raise
            self._pools[db_name] = pool
            logger.info("ap_tenant_db_connected", extra={"database": db_name})
            return pool

    async def _ensure_schema(self, pool: Any, key: str) -> None:
        if key in self._schema_ready:
            return
        try:
            await ensure_ap_schema(pool)
            self._schema_ready.add(key)
        except Exception as exc:
            logger.warning(
                "ap_schema_ensure_failed",
                extra={
                    "database": key,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
            )
            raise

    async def close(self) -> None:
        pools = list(self._pools.values())
        self._pools.clear()
        self._schema_ready.clear()
        for pool in pools:
            close = getattr(pool, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                await result
