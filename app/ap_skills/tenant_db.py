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

logger = logging.getLogger("orchestrator.ap_store")

_HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")
DEFAULT_TENANT_DB_PREFIX = "ezofis_Tenant_"


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
        self._lock = asyncio.Lock()

    async def acquire(self, tenant_id: str) -> Any:
        db_name = tenant_database_name(tenant_id, prefix=self._prefix)
        if db_name is None:
            return self._fallback_pool
        cached = self._pools.get(db_name)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._pools.get(db_name)
            if cached is not None:
                return cached
            url = replace_database_name(self._database_url, db_name)
            try:
                pool = await self._create_pool(
                    url, min_size=self._min_size, max_size=self._max_size
                )
            except Exception as exc:
                logger.warning(
                    "ap_tenant_db_connect_failed",
                    extra={"database": db_name, "error_type": type(exc).__name__, "error": str(exc)[:200]},
                )
                raise
            self._pools[db_name] = pool
            logger.info("ap_tenant_db_connected", extra={"database": db_name})
            return pool

    async def close(self) -> None:
        pools = list(self._pools.values())
        self._pools.clear()
        for pool in pools:
            close = getattr(pool, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                await result
