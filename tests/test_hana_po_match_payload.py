"""HANA PO invoice match request body (Core /purchase-orders/match)."""
from __future__ import annotations

import pytest

from app.ap_skills.hana_po import build_hana_po_invoice_match_body
from app.integrations.ezofis_client import EzofisClient


def test_build_hana_po_invoice_match_body_full_shape():
    po = {
        "vendor": "EV Parts Inc.",
        "currency": "USD",
        "total": 368.94,
        "match_items": [
            {
                "itemNumber": 10,
                "itemCategory": "Standard",
                "materialId": "MZ-RM-R100-02",
                "materialDescription": "BKR-100 Handle Bars",
                "materialGroup": "ZHANDLE",
                "plant": "1710",
                "orderQuantity": 129,
                "unitOfMeasure": "PC",
                "netPrice": 2.86,
                "priceUnit": 1,
                "netValue": 368.94,
            }
        ],
    }
    body = build_hana_po_invoice_match_body(
        instance_id="8eb40df7-9ae3-40d8-baea-86fe651f89d6",
        po_number="4500069456",
        invoice_number="56700989",
        status="Matched",
        invoice={
            "vendor": "EV Parts Inc.",
            "invoice_date": "2026-09-11",
            "currency": "USD",
            "total": 368.94,
        },
        po=po,
    )
    assert body["instanceId"] == "8eb40df7-9ae3-40d8-baea-86fe651f89d6"
    assert body["poNumber"] == "4500069456"
    assert body["invoiceNumber"] == "56700989"
    assert body["supplierName"] == "EV Parts Inc."
    assert body["invoiceDate"] == "2026-09-11"
    assert body["currency"] == "USD"
    assert body["totalAmount"] == 368.94
    assert body["status"] == "Matched"
    assert body["invoiceStatus"] == "Follow-On Documents"
    assert len(body["items"]) == 1
    assert body["items"][0]["materialId"] == "MZ-RM-R100-02"


def test_normalize_hana_po_populates_match_items():
    normalized = EzofisClient._normalize_hana_po_item(
        {
            "poNumber": "4500069456",
            "items": [{"itemNumber": 10, "materialId": "X", "orderQuantity": 1}],
        },
        po_number="4500069456",
    )
    assert normalized["match_items"] == [{"itemNumber": 10, "materialId": "X", "orderQuantity": 1}]


@pytest.mark.asyncio
async def test_save_hana_po_invoice_match_posts_full_body(monkeypatch):
    client = EzofisClient()
    captured: dict = {}

    async def fake_post(path, *, tenant_id, body):
        captured["path"] = path
        captured["body"] = body
        return {"updated": True, "created": True}

    monkeypatch.setattr(client, "_live_enabled", lambda: True)
    monkeypatch.setattr(client, "_post_json", fake_post)

    match_body = build_hana_po_invoice_match_body(
        instance_id="inst-1",
        po_number="4500069456",
        invoice_number="56700989",
        status="Matched",
        invoice={"vendor": "EV Parts Inc.", "total": 368.94},
        po={"match_items": [{"itemNumber": 10}]},
    )
    await client.save_hana_po_invoice_match(
        tenant_id="t1",
        connector_id="f7636e21-1a0c-457c-a2b4-e28430705477",
        body=match_body,
    )
    assert "hana/purchase-orders/match" in captured["path"]
    assert captured["body"]["supplierName"] == "EV Parts Inc."
    assert captured["body"]["items"] == [{"itemNumber": 10}]
