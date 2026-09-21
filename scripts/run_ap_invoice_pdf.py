#!/usr/bin/env python3
"""Run AP on a local invoice PDF (OCR extract + optional HANA PO lookup).

Example:
  py -3 scripts/run_ap_invoice_pdf.py "C:\\Users\\arasu\\Downloads\\PDF_Invoices\\PDF_Invoices\\Invoice_5105665738.pdf"
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

EZOFIS_TENANT = "b843b988-00ec-44e3-aca2-b8470133ef63"
HANA_CONNECTOR = "f7636e21-1a0c-457c-a2b4-e28430705477"

# From PDF text extract (Reference = PO in this invoice layout)
INVOICE_5105665738 = {
    "invoice_number": "5105665738/2026",
    "invoice_date": "2026-07-30",
    "vendor": "Grace Office-Supply Inc.",
    "po_number": "4500066847",
    "total": 100.0,
    "currency": "USD",
    "line_items": [{"description": "Y300_T Bike", "qty": 10, "amount": 100.0}],
}


def main() -> int:
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    use_file = pdf_path is not None and pdf_path.is_file()

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

    async def fake_create_pool(*args, **kwargs):
        return FakeDBPool()

    main_module.asyncpg.create_pool = fake_create_pool

    skills = [
        "extract_invoice",
        "po_lookup_sap",
        "po_match",
        "finalize_decision",
    ]

    with TestClient(main_module.app) as client:
        if use_file:
            print(f"AP file upload: {pdf_path}")
            with pdf_path.open("rb") as f:
                response = client.post(
                    "/chat",
                    data={
                        "session_id": "ap-pdf-5105665738",
                        "intent": "ap",
                        "tenant_id": EZOFIS_TENANT,
                        "resource": "HANA",
                        "connector_id": HANA_CONNECTOR,
                        "skills": json.dumps(skills),
                        "pageno": "1",
                    },
                    files={"file": (pdf_path.name, f, "application/pdf")},
                )
        else:
            print("AP invoice_json (pre-extracted from PDF)")
            response = client.post(
                "/chat",
                json={
                    "session_id": "ap-json-5105665738",
                    "intent": "ap",
                    "payload": {
                        "tenant_id": EZOFIS_TENANT,
                        "item_id": "invoice-5105665738",
                        "resource": "HANA",
                        "connector_id": HANA_CONNECTOR,
                        "skills": skills,
                        "invoice_json": INVOICE_5105665738,
                    },
                },
            )

    print("HTTP", response.status_code)
    if response.status_code != 200:
        print(response.text[:2000])
        return 1

    body = response.json()
    ap = body.get("ap_result") or {}
    arts = ap.get("artifacts") or {}
    print("decision:", ap.get("decision"))
    print("skills_run:", ap.get("skills_run"))
    if "extract_invoice" in arts:
        inv = (arts["extract_invoice"].get("invoice") or arts["extract_invoice"].get("data") or {})
        if isinstance(inv, dict):
            print(
                "extracted:",
                json.dumps(
                    {
                        k: inv.get(k)
                        for k in (
                            "invoice_number",
                            "po_number",
                            "vendor",
                            "total",
                            "currency",
                        )
                    },
                    indent=2,
                ),
            )
    if "po_lookup_sap" in arts:
        print("po_lookup_sap:", arts["po_lookup_sap"].get("reason"))
    if "po_match" in arts:
        pm = arts["po_match"]
        print("po_match:", pm.get("decision"), "-", pm.get("reason"))
    if "finalize_decision" in arts:
        print("finalize:", arts["finalize_decision"].get("reason", "")[:240])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
