"""Tests for HANA PO lookup error surfacing."""
from __future__ import annotations

import pytest

from app.integrations.ezofis_client import EzofisClient


@pytest.mark.asyncio
async def test_lookup_po_hana_surfaces_stopped_instance(monkeypatch):
    client = EzofisClient()

    async def fake_post(path, *, tenant_id, body):
        assert "hana/purchase-orders" in path
        return {
            "error": "HANA Cloud purchase-order query failed: HANA Database instance is stopped",
            "status_code": 400,
        }

    monkeypatch.setattr(client, "_live_enabled", lambda: True)
    monkeypatch.setattr(client, "_post_json", fake_post)

    result = await client.lookup_po_hana(
        tenant_id="t1",
        po_number="4500069456",
        connector_id="f7636e21-1a0c-457c-a2b4-e28430705477",
    )
    assert result["lookup_error"] == "hana_unavailable"
    assert "stopped" in result["reason"].lower()


@pytest.mark.asyncio
async def test_lookup_po_hana_not_found_when_empty_items(monkeypatch):
    client = EzofisClient()

    async def fake_post(path, *, tenant_id, body):
        return {"found": False, "count": 0, "items": []}

    monkeypatch.setattr(client, "_live_enabled", lambda: True)
    monkeypatch.setattr(client, "_post_json", fake_post)

    result = await client.lookup_po_hana(
        tenant_id="t1",
        po_number="4500069456",
        connector_id="f7636e21-1a0c-457c-a2b4-e28430705477",
    )
    assert result["lookup_error"] == "not_found"
