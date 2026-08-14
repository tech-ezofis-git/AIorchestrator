#!/usr/bin/env python3
"""Smoke test for OCR document job (mocked OCR + LLM — no live Azure/LLM required).

Run:
  py -3 -m scripts.smoke_ocr_document
or:
  py -3 scripts/smoke_ocr_document.py
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

    async def fake_completion(self, messages):
        return {
            "content": json.dumps(
                {
                    "ocrResult": [
                        {"name": "Invoice No", "value": "INV/26-27/002140", "type": "SHORT_TEXT"},
                        {"name": "Due Date", "value": "2026-05-20", "type": "DATE"},
                    ],
                    "tableResult": [],
                }
            ),
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

    from app.llm.adapter import LLMAdapter

    LLMAdapter.chat_completion = fake_completion  # type: ignore[method-assign]

    failures = 0
    with TestClient(main_module.app) as client:
        # 1) JSON + relative blob path (mock OCR text)
        r1 = client.post(
            "/chat",
            json={
                "session_id": "smoke-json",
                "intent": "ocr",
                "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
                "payload": {
                    "filepath": r"INV26-27002140.pdf",
                    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                    "pageno": "1",
                    "parameters": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
                    "tableparameters": [],
                },
            },
        )
        print("JSON blob path:", r1.status_code)
        if r1.status_code != 200:
            print(r1.text)
            failures += 1
        else:
            reply = json.loads(r1.json()["reply"])
            print("  ocrResult fields:", len(reply.get("ocrResult", [])))
            print("  has tableResult:", "tableResult" in reply)
            print("  has ocr_json:", "ocr_json" in reply)
            print("  has nested tokens:", "tokens" in reply)
            if not reply.get("ocrResult") or "ocr_json" in reply or "tokens" in reply:
                failures += 1

        # 2) multipart file upload
        r2 = client.post(
            "/chat",
            data={
                "session_id": "smoke-mp",
                "intent": "ocr",
                "pageno": "1",
                "parameters": json.dumps(["Invoice No,SHORT_TEXT"]),
            },
            files={"file": ("inv.pdf", b"%PDF-1.4 Invoice No INV/26-27/002140", "application/pdf")},
        )
        print("multipart file:", r2.status_code)
        if r2.status_code != 200:
            print(r2.text)
            failures += 1

        # 3) invalid pageno
        r3 = client.post(
            "/chat",
            json={
                "session_id": "smoke-bad",
                "intent": "ocr",
                "payload": {"filepath": "c/a.pdf", "pageno": "99"},
            },
        )
        print("invalid pageno:", r3.status_code, "(expect 400)")
        if r3.status_code != 400:
            failures += 1

        # 4) health
        r4 = client.get("/health")
        print("health:", r4.status_code)
        if r4.status_code != 200:
            failures += 1

    get_settings.cache_clear()
    if failures:
        print(f"SMOKE FAILED ({failures})")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
