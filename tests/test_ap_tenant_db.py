"""AP tenant database routing: ezofis_Tenant_{first 8 of tenant_id}."""
import asyncio

from app.ap_skills.tenant_db import (
    ApTenantDbPools,
    ezfb_items_table,
    replace_database_name,
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
    fake = FakeDBPool()

    async def create_pool(url, **kwargs):
        seen.append(url)
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
