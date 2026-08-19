"""Apply catalog DDL + seed to CATALOG_DATABASE_URL. Not imported by the app."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg

from app.catalog.store import CatalogStore
from app.catalog.url import catalog_pool_kwargs, normalize_catalog_url
from app.config import Settings
from app.llm.model_presets import MODEL_PRESETS


async def main() -> None:
    settings = Settings()
    url = (settings.catalog_database_url or "").strip()
    if not url:
        raise SystemExit("CATALOG_DATABASE_URL is not set")
    conn = await asyncpg.connect(normalize_catalog_url(url), **catalog_pool_kwargs(url))
    try:
        store = CatalogStore(conn)
        await store.ensure_schema()
        await store.seed_defaults(MODEL_PRESETS, settings)
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'catalog_%' "
            "ORDER BY table_name"
        )
        agents = await conn.fetchval("SELECT count(*) FROM catalog_agents")
        models = await conn.fetchval("SELECT count(*) FROM catalog_models")
        print("tables", [row["table_name"] for row in tables])
        print("catalog_agents", int(agents))
        print("catalog_models", int(models))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
