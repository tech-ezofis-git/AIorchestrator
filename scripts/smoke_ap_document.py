#!/usr/bin/env python3
"""Smoke test for AP document jobs (mocked OCR/credits — no live Ezofis required).

Run:
  py -3 -m scripts.smoke_ap_document
or:
  py -3 scripts/smoke_ap_document.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("OCR_EXTRACT_URL", "")
os.environ.setdefault("QWEN_MAC_API_KEY", "smoke-key")
os.environ.setdefault("AZURE_SOUTH_INDIA_API_KEY", "smoke-key")
os.environ.setdefault("AZURE_EAST_US_API_KEY", "smoke-key")
# Keep credits/masters mocked for offline smoke.
os.environ.setdefault("EZOFIS_LOGIN_EMAIL", "")
os.environ.setdefault("EZOFIS_LOGIN_PASSWORD", "")

SAMPLE_INVOICE = {
    "invoice_number": "INV-100",
    "vendor": "ACME Supplies",
    "po_number": "PO-1",
    "total": 1234.56,
    "currency": "USD",
    "line_items": [{"description": "Widget", "qty": 10, "amount": 1234.56}],
}


def main() -> int:
    import fakeredis.aioredis
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.config import get_settings
    from tests.fakes import FakeDBPool

    class _IsolatedFakeRedis:
        @staticmethod
        def from_url(url, **kwargs):
            return fakeredis.aioredis.FakeRedis(**kwargs)

    get_settings.cache_clear()
    main_module.Redis = _IsolatedFakeRedis
    fake_db = FakeDBPool()

    async def fake_create_pool(*args, **kwargs):
        return fake_db

    main_module.asyncpg.create_pool = fake_create_pool

    failures = 0
    with TestClient(main_module.app) as client:
        # 1) Full default plan with invoice_json
        r1 = client.post(
            "/chat",
            json={
                "session_id": "smoke-ap",
                "intent": "ap",
                "payload": {
                    "tenant_id": "smoke-tenant",
                    "item_id": "smoke-doc-1",
                    "invoice_json": SAMPLE_INVOICE,
                },
            },
        )
        if r1.status_code != 200:
            print("FAIL full plan:", r1.status_code, r1.text)
            failures += 1
        else:
            ap = r1.json().get("ap_result") or {}
            ok = (
                ap.get("credits_charged") == 6
                and ap.get("decision") == "MATCHED"
                and "finalize_decision" in (ap.get("skills_run") or [])
            )
            print("OK full plan" if ok else "FAIL full plan shape", json.dumps(ap, indent=2)[:500])
            failures += 0 if ok else 1

        # 2) Vendor-only re-run on same item_id
        r2 = client.post(
            "/chat",
            json={
                "session_id": "smoke-ap-2",
                "intent": "ap",
                "payload": {
                    "tenant_id": "smoke-tenant",
                    "item_id": "smoke-doc-1",
                    "skills": ["vendor_validate"],
                },
            },
        )
        if r2.status_code != 200:
            print("FAIL vendor re-run:", r2.status_code, r2.text)
            failures += 1
        else:
            ap = r2.json().get("ap_result") or {}
            ok = ap.get("skills_run") == ["vendor_validate"] and ap.get("credits_charged") == 1
            print("OK vendor re-run" if ok else "FAIL vendor re-run", ap.get("skills_run"), ap.get("credits_charged"))
            failures += 0 if ok else 1

        # 3) Tenant plan disables backorder
        fake_db.ap_tenant_plans["smoke-tenant"] = {
            "enabled_skills": [
                "extract_invoice",
                "po_match",
                "duplicate_detect",
                "vendor_validate",
                "finalize_decision",
            ],
            "thresholds": {},
        }
        r3 = client.post(
            "/chat",
            json={
                "session_id": "smoke-ap-3",
                "intent": "ap",
                "payload": {
                    "tenant_id": "smoke-tenant",
                    "item_id": "smoke-doc-2",
                    "invoice_json": SAMPLE_INVOICE,
                },
            },
        )
        if r3.status_code != 200:
            print("FAIL gated plan:", r3.status_code, r3.text)
            failures += 1
        else:
            skills = (r3.json().get("ap_result") or {}).get("skills_run") or []
            ok = "backorder_detect" not in skills and len(skills) == 5
            print("OK gated plan" if ok else "FAIL gated plan", skills)
            failures += 0 if ok else 1

        # 4) Legacy AP status Q&A still works
        async def fake_completion(self, messages):
            return {
                "content": "Invoice INV-1234 is Approved.",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        from app.llm.adapter import LLMAdapter

        LLMAdapter.chat_completion = fake_completion  # type: ignore[method-assign]
        r4 = client.post(
            "/chat",
            json={"session_id": "smoke-ap-legacy", "message": "status of invoice INV-1234"},
        )
        if r4.status_code != 200 or r4.json().get("invoice_reference") != "INV-1234":
            print("FAIL legacy AP:", r4.status_code, r4.text)
            failures += 1
        else:
            print("OK legacy AP Q&A")

        print(
            f"artifacts={len(fake_db.ap_skill_artifacts)} "
            f"credits={len(fake_db.ap_credit_ledger)} "
            f"runs={len(fake_db.ap_runs)}"
        )

    get_settings.cache_clear()
    if failures:
        print(f"SMOKE FAILED ({failures})")
        return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
