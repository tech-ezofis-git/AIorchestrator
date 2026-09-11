"""Phase 1 live write via Ezofis API: merge sample POs into SAP connector ConfigJson."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(r"D:\ezofis\v6\orchestrator")
ENV_PATH = ROOT / ".env"

SAMPLES = [
    {
        "po_number": "PO-60001",
        "vendor": "ACME Supplies",
        "total": 1500.00,
        "currency": "USD",
        "lines": [
            {"description": "Widget A", "qty": 10, "unit_price": 100, "amount": 1000},
            {"description": "Widget B", "qty": 5, "unit_price": 100, "amount": 500},
        ],
    },
    {
        "po_number": "PO-SAP-1001",
        "vendor": "Contoso Trading",
        "total": 2500.00,
        "currency": "USD",
        "lines": [
            {"description": "Service retainer", "qty": 1, "unit_price": 2500, "amount": 2500},
        ],
    },
    {
        "po_number": "PO-SAP-1002",
        "vendor": "Fabrikam Ltd",
        "total": 875.50,
        "currency": "USD",
        "lines": [
            {"description": "Parts kit", "qty": 1, "unit_price": 875.50, "amount": 875.50},
        ],
    },
]

PATCH = {
    "provider": "SAP",
    "mode": "sample",
    "samplePurchaseOrders": SAMPLES,
}


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env(ENV_PATH)
    base = (env.get("EZOFIS_API_BASE") or "").rstrip("/")
    email = (env.get("EZOFIS_LOGIN_EMAIL") or "").strip()
    password = (env.get("EZOFIS_LOGIN_PASSWORD") or "").strip()
    if not base or not email or not password:
        print("Missing EZOFIS_API_BASE / EZOFIS_LOGIN_EMAIL / EZOFIS_LOGIN_PASSWORD", file=sys.stderr)
        return 1

    print(f"api_base={base}")
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        tenants_resp = client.get(f"{base}/auth/tenants", params={"email": email})
        print(f"tenants_status={tenants_resp.status_code}")
        tenants_resp.raise_for_status()
        tenants_data = tenants_resp.json()
        tenants = tenants_data if isinstance(tenants_data, list) else tenants_data.get("tenants") or tenants_data.get("items") or []
        if not tenants:
            print("No tenants for login email", file=sys.stderr)
            return 2

        # Prefer EZOFIS / known first-product tenant, else first in list.
        preferred_ids = {
            "b843b988-00ec-44e3-aca2-b8470133ef63",
        }
        preferred_names = {"ezofis"}
        ordered = []
        for t in tenants:
            tid = str(t.get("tenantId") or t.get("tenant_id") or t.get("id") or "")
            name = str(t.get("name") or t.get("tenantName") or "")
            score = 0
            if tid.lower() in preferred_ids:
                score += 10
            if name.strip().lower() in preferred_names:
                score += 5
            ordered.append((score, t))
        ordered.sort(key=lambda x: (-x[0], str(x[1].get("name") or "")))

        last_err = None
        for score, first in ordered:
            tenant_id = str(first.get("tenantId") or first.get("tenant_id") or first.get("id") or "")
            tenant_name = first.get("name") or first.get("tenantName") or ""
            print(f"try_tenant={tenant_id} name={tenant_name!r} score={score}")

            login_resp = client.post(
                f"{base}/auth/ezofis/login",
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Tenant-Id": tenant_id,
                },
                json={"email": email, "password": password},
            )
            print(f"login_status={login_resp.status_code}")
            if login_resp.status_code >= 400:
                last_err = login_resp.text[:300]
                print(f"login_failed={last_err}")
                continue
            login = login_resp.json()
            token = login.get("accessToken") or login.get("access_token") or login.get("token")
            if not token:
                last_err = "no token"
                continue
            headers = {
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
                "Content-Type": "application/json",
                "X-Tenant-Id": tenant_id,
            }

            list_resp = client.get(f"{base}/connector/all", headers=headers)
            if list_resp.status_code == 404:
                list_resp = client.get(f"{base}/api/connector/all", headers=headers)
            print(f"list_status={list_resp.status_code}")
            if list_resp.status_code >= 400:
                list_resp = client.post(f"{base}/connector/all", headers=headers, json={"mode": "browse"})
                if list_resp.status_code == 404:
                    list_resp = client.post(f"{base}/api/connector/all", headers=headers, json={"mode": "browse"})
                print(f"list_post_status={list_resp.status_code}")
            if list_resp.status_code >= 400:
                last_err = list_resp.text[:300]
                continue
            payload = list_resp.json()
            items = payload.get("items") if isinstance(payload, dict) else payload
            if items is None and isinstance(payload, dict):
                items = payload.get("Items")
            items = items or []
            print(f"connectors={len(items)}")

            sap = []
            for item in items:
                code = str(item.get("providerCode") or item.get("ProviderCode") or "").upper()
                # Live tenant uses SAP_XSUAA; also accept SAP / SAP_* .
                if code == "SAP" or code.startswith("SAP_") or "SAP" in code:
                    sap.append(item)
            if not sap:
                codes = sorted(
                    {str(i.get("providerCode") or i.get("ProviderCode") or "") for i in items}
                )
                print(f"no_sap provider_codes={codes}")
                continue

            sap.sort(
                key=lambda i: (
                    0 if str(i.get("providerCode") or i.get("ProviderCode") or "").upper() in ("SAP", "SAP_XSUAA") else 1,
                    not bool(i.get("isDefault") or i.get("IsDefault")),
                    str(i.get("name") or ""),
                )
            )
            target = sap[0]
            connector_id = str(target.get("id") or target.get("Id"))
            name = target.get("name") or target.get("Name")
            provider_code = str(target.get("providerCode") or target.get("ProviderCode") or "SAP")
            raw_cfg = target.get("configJson") or target.get("ConfigJson") or "{}"
            if isinstance(raw_cfg, dict):
                existing = raw_cfg
            else:
                try:
                    existing = json.loads(raw_cfg) if str(raw_cfg).strip() else {}
                except json.JSONDecodeError:
                    existing = {}
                if not isinstance(existing, dict):
                    existing = {}
            preserved = sorted(set(existing) - set(PATCH))
            merged = {**existing, **PATCH}
            new_cfg = json.dumps(merged, ensure_ascii=False)

            put_url = f"{base}/connector/{connector_id}"
            put_body = {
                "name": name,
                "providerCode": provider_code,
                "configJson": new_cfg,
                "isDefault": bool(target.get("isDefault") or target.get("IsDefault")),
            }
            put_resp = client.put(put_url, headers=headers, json=put_body)
            if put_resp.status_code == 404:
                put_url = f"{base}/api/connector/{connector_id}"
                put_resp = client.put(put_url, headers=headers, json=put_body)
            print(f"put_status={put_resp.status_code} url={put_url} provider={provider_code}")
            if put_resp.status_code >= 400:
                print(put_resp.text[:500], file=sys.stderr)
                last_err = put_resp.text[:300]
                continue

            body = put_resp.json() if put_resp.content else {}
            verify_cfg = body.get("configJson") or body.get("ConfigJson") or new_cfg
            if isinstance(verify_cfg, str):
                try:
                    verify_obj = json.loads(verify_cfg)
                except json.JSONDecodeError:
                    verify_obj = {}
            else:
                verify_obj = verify_cfg if isinstance(verify_cfg, dict) else {}
            pos = [p.get("po_number") for p in (verify_obj.get("samplePurchaseOrders") or [])]

            summary = {
                "tenant_id": tenant_id,
                "tenant_name": tenant_name,
                "connector_id": connector_id,
                "connector_name": name,
                "provider_code": provider_code,
                "mode": verify_obj.get("mode"),
                "sample_po_numbers": pos,
                "preserved_keys": preserved,
                "api_base": base,
            }
            out = ROOT / "deploy" / "SAP_CONNECTOR_SAMPLE_PO_LIVE.json"
            out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            print(f"wrote {out}")
            return 0

        print(f"FAILED: no SAP connector updated. last_err={last_err}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
