"""Guards against false MATCHED when PoMaster is empty or invoice form is abused."""
from __future__ import annotations

import pytest

from app.ap_skills.po_match import run as run_po_match
from app.ap_skills.types import ApContext


class _FakeEzofis:
    async def lookup_po(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_po_match_refuses_invoice_form_as_master():
    ctx = ApContext(
        tenant_id="t1",
        item_key="i1",
        run_id="r1",
        session_id="s1",
        invoice_json={"po_number": "PO-1", "vendor": "ACME", "total": 100},
        artifacts={},
        settings=type("S", (), {"ap_approved_threshold": 80, "ap_partial_threshold": 50, "ap_amount_tolerance": 0.05})(),
        ezofis=_FakeEzofis(),
        llm=None,
        dispatcher=None,
        store=None,
        document_job={"form_id": "invoice-form", "master_source": "InternalForm"},
        thresholds={},
        form_id="invoice-form",
    )
    result = await run_po_match(ctx)
    assert result.data["decision"] == "NOT_MATCHED"
    assert "refusing" in (result.data.get("reason") or "").lower()


@pytest.mark.asyncio
async def test_po_match_caps_matched_without_vendor():
    class Ez:
        async def lookup_po(self, **kwargs):
            return {
                "po_number": "PO-1",
                "vendor": None,
                "total": 100.0,
                "source": "form",
                "form_id": "master-form",
            }

    ctx = ApContext(
        tenant_id="t1",
        item_key="i1",
        run_id="r1",
        session_id="s1",
        invoice_json={"po_number": "PO-1", "vendor": "ACME", "total": 100},
        artifacts={},
        settings=type("S", (), {"ap_approved_threshold": 70, "ap_partial_threshold": 50, "ap_amount_tolerance": 0.05})(),
        ezofis=Ez(),
        llm=None,
        dispatcher=None,
        store=None,
        document_job={
            "form_id": "invoice-form",
            "master_form_id": "master-form",
            "master_source": "InternalForm",
        },
        thresholds={"approved": 70, "partial": 50},
        form_id="invoice-form",
    )
    result = await run_po_match(ctx)
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert "Capped" in (result.data.get("reason") or "")
