"""AP tenant database routing: ezofis_Tenant_{first 8 of tenant_id}."""
import asyncio

from app.ap_skills.tenant_db import (
    ApTenantDbPools,
    ezfb_items_table,
    replace_database_name,
    repository_items_table,
    tenant_database_name,
)
from tests.fakes import FakeDBPool


def test_uuid_tenant_maps_to_ezofis_tenant_db():
    assert (
        tenant_database_name("2e3b7b37-38a3-4f94-878e-a006dad93230")
        == "ezofis_Tenant_2e3b7b37"
    )


def test_ezfb_items_table_from_guid_and_numeric():
    assert (
        ezfb_items_table("29171de4-e210-466e-9e90-40fa9fa4354d")
        == "ezfb_29171de4_items"
    )
    assert ezfb_items_table("98") == "ezfb_98_items"
    assert ezfb_items_table("  ") is None
    assert ezfb_items_table(None) is None


def test_repository_items_table_from_guid():
    assert (
        repository_items_table("38b1b6dd-854b-489f-aa44-ac6d4dd691e8")
        == "items_38b1b6dd"
    )
    assert repository_items_table("98") is None
    assert repository_items_table("") is None
    assert repository_items_table(None) is None


def test_short_hex_prefix_maps_to_tenant_db():
    assert tenant_database_name("2e3b7b37") == "ezofis_Tenant_2e3b7b37"


def test_local_tenant_ids_stay_on_main_db():
    assert tenant_database_name("t-ap") is None
    assert tenant_database_name("default") is None
    assert tenant_database_name("smoke-tenant") is None
    assert tenant_database_name("") is None


def test_empty_prefix_disables_routing():
    assert (
        tenant_database_name(
            "2e3b7b37-38a3-4f94-878e-a006dad93230", prefix=""
        )
        is None
    )


def test_replace_database_name_keeps_auth_and_query():
    url = replace_database_name(
        "postgresql://v6dbadmin:secret@v6app:5432/maindb?sslmode=require",
        "ezofis_Tenant_2e3b7b37",
    )
    assert url == (
        "postgresql://v6dbadmin:secret@v6app:5432/ezofis_Tenant_2e3b7b37?sslmode=require"
    )


def test_uuid_tenant_opens_rewritten_database_url():
    seen = []
    seen_kwargs = []
    fake = FakeDBPool()

    async def create_pool(url, **kwargs):
        seen.append(url)
        seen_kwargs.append(kwargs)
        return fake

    async def run():
        pools = ApTenantDbPools(
            "postgresql://v6dbadmin:secret@v6app:5432/maindb?sslmode=require",
            fallback_pool=object(),
            create_pool=create_pool,
        )
        db = await pools.acquire("2e3b7b37-38a3-4f94-878e-a006dad93230")
        again = await pools.acquire("2e3b7b37-38a3-4f94-878e-a006dad93230")
        local = await pools.acquire("t-ap")
        await pools.close()
        return db, again, local

    db, again, local = asyncio.run(run())
    assert db is fake
    assert again is fake
    assert local is not fake
    assert len(seen) == 1
    assert "/ezofis_Tenant_2e3b7b37" in seen[0]
    assert "maindb" not in seen[0].split("?")[0]
    # asyncpg gets ssl= via kwargs; sslmode query is stripped
    assert "sslmode" not in seen[0].lower()
    assert seen_kwargs[0].get("ssl") is True


def test_asyncpg_url_from_ado_net_connection_string():
    from app.catalog.url import asyncpg_url_from_connection_string

    url = asyncpg_url_from_connection_string(
        "Host=ezv6psql.postgres.database.azure.com;Database=ezofis_Tenant_b843b988;"
        "Username=v6dbadmin;Password=s3cret;SSL Mode=Require"
    )
    assert url.startswith("postgresql://v6dbadmin:s3cret@ezv6psql.postgres.database.azure.com:5432/ezofis_Tenant_b843b988")
    assert "sslmode=Require" in url or "sslmode=require" in url.lower()


def test_acquire_for_global_search_prefers_catalog_connection_string():
    seen = []
    cs_pool = FakeDBPool()
    derived_pool = FakeDBPool()

    async def create_pool(url, **kwargs):
        seen.append(url)
        if "from_cs" in url:
            return cs_pool
        return derived_pool

    class Store:
        async def fetch_tenant_connection_string(self, tenant_id: str):
            assert tenant_id.startswith("2e3b7b37")
            return "postgresql://u:p@ezv6psql.postgres.database.azure.com:5432/from_cs?sslmode=require"

    async def run():
        pools = ApTenantDbPools(
            "postgresql://v6dbadmin:secret@v6app:5432/maindb?sslmode=require",
            fallback_pool=object(),
            create_pool=create_pool,
        )
        db = await pools.acquire_for_global_search(
            "2e3b7b37-38a3-4f94-878e-a006dad93230", Store()
        )
        await pools.close()
        return db

    db = asyncio.run(run())
    assert db is cs_pool
    assert any("from_cs" in u for u in seen)
