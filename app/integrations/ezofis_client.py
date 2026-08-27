"""EZOFIS API client.

Document/report/invoice-status helpers remain deterministic mocks (used by
Search/Summary/Insight/legacy AP Q&A). AP document-job skills call the
cloud API when EZOFIS_LOGIN_EMAIL + EZOFIS_LOGIN_PASSWORD are set; otherwise
credits and PO/vendor masters stay mocked so unit tests need no network.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("orchestrator.ezofis")


class EzofisClient:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings
        self.credit_charges: list[dict[str, Any]] = []
        self._token: Optional[str] = None
        self._token_type: str = "Bearer"
        self._auth_tenant_id: Optional[str] = None

    def _cfg(self) -> Settings:
        if self._settings is None:
            return get_settings()
        return self._settings

    def _live_enabled(self) -> bool:
        cfg = self._cfg()
        return bool((cfg.ezofis_login_email or "").strip() and (cfg.ezofis_login_password or "").strip())

    def _base(self) -> str:
        return (self._cfg().ezofis_api_base or "https://cloud.ezofis.com/api").rstrip("/")

    async def get_user_context(self, user_id: str) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "display_name": "Mock User",
            "roles": ["employee"],
            "department": "Unknown",
            "mock": True,
        }

    async def get_document(self, document_id: str) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "title": "Mock Document",
            "content": None,
            "mock": True,
        }

    async def fetch_document(self, document_id: str) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "title": f"Mock Document {document_id}",
            "content": (
                f"This is placeholder content for EZOFIS document '{document_id}'. "
                "In a real integration this would be the document's actual text "
                "(or an extracted/OCR'd version of it) fetched from EZOFIS. "
                "For now it exists only so the Summary agent has real text to "
                "summarize during development and testing."
            ),
            "mock": True,
        }

    async def fetch_invoice_status(self, invoice_reference: str) -> dict[str, Any]:
        digest = hashlib.sha256(invoice_reference.encode()).hexdigest()
        statuses = ("Pending Approval", "Approved", "Paid", "Overdue")
        status = statuses[int(digest[:2], 16) % len(statuses)]
        amount = round(500 + (int(digest[2:8], 16) % 2_000_000) / 100, 2)
        return {
            "invoice_reference": invoice_reference,
            "status": status,
            "amount": amount,
            "currency": "USD",
            "vendor": f"Mock Vendor for {invoice_reference}",
            "invoice_date": "2026-06-01",
            "due_date": "2026-07-01",
            "approver": "Mock Approver",
            "mock": True,
        }

    async def fetch_report_data(self, report_id: str) -> dict[str, Any]:
        return {
            "report_id": report_id,
            "data_points": [
                {"label": "Open Invoices", "value": 42},
                {"label": "Overdue Invoices", "value": 7},
                {"label": "Total Outstanding ($)", "value": 128450.75},
                {"label": "Avg Days to Payment", "value": 18.4},
            ],
            "mock": True,
        }

    async def authenticate(self, *, tenant_id: Optional[str] = None) -> dict[str, str]:
        """GET /auth/tenants then POST /auth/ezofis/login (async port of apagentv6)."""
        cfg = self._cfg()
        email = (cfg.ezofis_login_email or "").strip()
        password = (cfg.ezofis_login_password or "").strip()
        if not email or not password:
            raise RuntimeError("EZOFIS_LOGIN_EMAIL and EZOFIS_LOGIN_PASSWORD are required for live AP calls.")
        timeout = cfg.ezofis_timeout_seconds
        requested = (tenant_id or "").strip()
        async with httpx.AsyncClient(timeout=timeout) as client:
            tenants_resp = await client.get(
                f"{self._base()}/auth/tenants",
                params={"email": email},
                headers={"accept": "application/json"},
            )
            tenants_resp.raise_for_status()
            payload = tenants_resp.json() if tenants_resp.content else {}
            tenants = payload.get("tenants") if isinstance(payload, dict) else None
            if not isinstance(tenants, list) or not tenants:
                raise RuntimeError("Ezofis login returned no tenants.")
            login_tenant = tenants[0]
            if requested:
                login_tenant = next(
                    (
                        t
                        for t in tenants
                        if isinstance(t, dict)
                        and str(t.get("tenantId") or "").strip().lower() == requested.lower()
                    ),
                    None,
                )
                if login_tenant is None:
                    raise RuntimeError(f"Tenant {requested!r} is not available for {email}.")
            resolved_tenant = str(login_tenant.get("tenantId") or requested)
            login_resp = await client.post(
                f"{self._base()}/auth/ezofis/login",
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Tenant-Id": resolved_tenant,
                },
                json={"email": email, "password": password},
            )
            login_resp.raise_for_status()
            login_data = login_resp.json() if login_resp.content else {}
        token = str(login_data.get("access_token") or login_data.get("token") or "")
        if not token:
            raise RuntimeError("Ezofis login did not return an access token.")
        self._token = token
        self._token_type = str(login_data.get("token_type") or "Bearer")
        self._auth_tenant_id = resolved_tenant
        return {
            "access_token": token,
            "token_type": self._token_type,
            "tenant_id": resolved_tenant,
        }

    async def list_tenants(self) -> list[dict[str, str]]:
        """GET /auth/tenants for the configured login email. Empty when login is unset."""
        cfg = self._cfg()
        email = (cfg.ezofis_login_email or "").strip()
        if not email:
            return []
        timeout = min(float(cfg.ezofis_timeout_seconds or 15), 8.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                tenants_resp = await client.get(
                    f"{self._base()}/auth/tenants",
                    params={"email": email},
                    headers={"accept": "application/json"},
                )
                tenants_resp.raise_for_status()
                payload = tenants_resp.json() if tenants_resp.content else {}
                tenants = payload.get("tenants") if isinstance(payload, dict) else None
        except Exception:
            logger.warning("ezofis_list_tenants_failed")
            return []
        if not isinstance(tenants, list):
            return []
        rows: list[dict[str, str]] = []
        for item in tenants:
            if not isinstance(item, dict):
                continue
            tenant_id = str(item.get("tenantId") or item.get("tenant_id") or item.get("id") or "").strip()
            if not tenant_id:
                continue
            name = str(
                item.get("tenantName")
                or item.get("name")
                or item.get("companyName")
                or tenant_id
            ).strip()
            rows.append({"id": tenant_id, "name": name or tenant_id})
        return rows

    async def _auth_headers(self, tenant_id: str) -> dict[str, str]:
        if not self._token:
            await self.authenticate(tenant_id=tenant_id)
        tenant = tenant_id or self._auth_tenant_id or ""
        return {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"{self._token_type} {self._token}",
            "X-Tenant-Id": tenant,
        }

    async def charge_activity_credit(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        identify: str,
        credit: int = 1,
        remarks: str = "",
    ) -> dict[str, Any]:
        """POST /billing/credits/update — 1 credit per executed AP skill."""
        payload = {
            "activityType": "AP_AGENT",
            "subActivity": skill_id,
            "identify": identify or "Document",
            "remarks": remarks or skill_id,
            "credit": int(credit),
            "env": self._cfg().ezofis_env,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
        }
        self.credit_charges.append({"tenant_id": tenant_id, **payload})
        if not self._live_enabled():
            return {"status": "mocked", "mock": True, "credit": credit, "subActivity": skill_id}
        try:
            headers = await self._auth_headers(tenant_id)
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(
                    f"{self._base()}/billing/credits/update",
                    headers=headers,
                    json=payload,
                )
                if response.status_code not in (200, 201, 204):
                    logger.warning(
                        "ezofis_credit_failed",
                        extra={"status_code": response.status_code, "skill_id": skill_id},
                    )
                    return {"status": "failed", "status_code": response.status_code}
                if response.content:
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            return body
                    except Exception:
                        return {"status": "charged", "raw": response.text[:200]}
                return {"status": "charged"}
        except Exception:
            logger.warning("ezofis_credit_error", extra={"skill_id": skill_id})
            return {"status": "failed"}

    async def lookup_po(
        self, *, tenant_id: str, po_number: str, form_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        if not po_number:
            return None
        from app.ap_skills.tenant_db import ezfb_items_table

        table = ezfb_items_table(form_id)
        params: dict[str, Any] = {"po_number": po_number}
        if form_id:
            params["form_id"] = str(form_id).strip()
        if table:
            params["table"] = table
        if self._live_enabled():
            live = await self._get_master("/masters/po", tenant_id=tenant_id, params=params)
            if live:
                return live
        mock: dict[str, Any] = {
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "currency": "USD",
            "lines": [
                {"id": "1", "description": "Widget", "qty": 10, "price": 123.456, "amount": 1234.56}
            ],
            "mock": True,
        }
        if form_id:
            mock["form_id"] = str(form_id).strip()
        if table:
            mock["ezfb_table"] = table
        return mock

    async def lookup_vendor(self, *, tenant_id: str, vendor_name: str) -> Optional[dict[str, Any]]:
        if not vendor_name:
            return None
        if self._live_enabled():
            live = await self._get_master(
                "/masters/vendor", tenant_id=tenant_id, params={"name": vendor_name}
            )
            if live:
                return live
        return {"name": vendor_name, "vendor": vendor_name, "status": "ACTIVE", "mock": True}

    async def lookup_invoice_history(
        self, *, tenant_id: str, invoice_number: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if self._live_enabled() and invoice_number:
            live = await self._get_master(
                "/ap/invoices/history",
                tenant_id=tenant_id,
                params={"invoice_number": invoice_number},
            )
            if isinstance(live, list):
                return live
            if isinstance(live, dict) and isinstance(live.get("items"), list):
                return live["items"]
        return []

    async def lookup_gl_accounts(self, *, tenant_id: str) -> dict[str, Any]:
        if self._live_enabled():
            live = await self._get_master("/masters/gl", tenant_id=tenant_id, params={})
            if isinstance(live, dict):
                return live
            if isinstance(live, list):
                return {"accounts": live}
        return {
            "accounts": [
                {"gl_account": "6100", "category": "Widget", "name": "Office Supplies"},
                {"gl_account": "6200", "category": "Travel", "name": "Travel Expense"},
            ],
            "mock": True,
        }

    async def lookup_grn(
        self,
        *,
        tenant_id: str,
        grn_number: Optional[str] = None,
        po_number: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        params: dict[str, Any] = {}
        if grn_number:
            params["grn_number"] = grn_number
        if po_number:
            params["po_number"] = po_number
        if self._live_enabled() and params:
            live = await self._get_master("/masters/grn", tenant_id=tenant_id, params=params)
            if isinstance(live, dict):
                return live
        if not po_number and not grn_number:
            return None
        return {
            "grn_number": grn_number or f"GRN-{po_number}",
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "lines": [{"id": "1", "description": "Widget", "qty": 10, "received_qty": 10}],
            "mock": True,
        }

    async def lookup_matter(
        self,
        *,
        tenant_id: str,
        matter_id: str,
        matter_master_id: Any = None,
    ) -> Optional[dict[str, Any]]:
        if not matter_id:
            return None
        params: dict[str, Any] = {"matter_id": matter_id}
        if matter_master_id is not None:
            params["matter_master_id"] = matter_master_id
        if self._live_enabled():
            live = await self._get_master("/masters/matter", tenant_id=tenant_id, params=params)
            if isinstance(live, dict):
                return live
        # Deterministic mock fallback list (same spirit as apagentv6).
        fallback = {
            "M-001": {"matter_id": "M-001", "client_name": "Acme Corp", "mock": True},
            "M-002": {"matter_id": "M-002", "client_name": "Beta Holdings", "mock": True},
            "610882": {"matter_id": "610882", "client_name": "Test Client", "mock": True},
        }
        return fallback.get(matter_id.strip().upper()) or fallback.get(matter_id.strip())

    async def lookup_po_quickbooks(
        self, *, tenant_id: str, po_number: str, connector_id: str
    ) -> Optional[dict[str, Any]]:
        if not po_number:
            return None
        if self._live_enabled():
            live = await self._post_json(
                f"/connector/{connector_id}/quickbooks/purchase-orders/lookup",
                tenant_id=tenant_id,
                body={"poNumber": po_number},
            )
            if isinstance(live, dict):
                items = live.get("items") or live.get("purchaseOrders") or []
                if isinstance(items, list) and items:
                    item = items[0] if isinstance(items[0], dict) else {}
                    return {
                        "po_number": po_number,
                        "vendor": (item.get("VendorRef") or {}).get("name")
                        if isinstance(item.get("VendorRef"), dict)
                        else item.get("vendorName"),
                        "total": item.get("TotalAmt") or item.get("total"),
                        "lines": item.get("Line") or item.get("lines") or [],
                        "source": "quickbooks",
                    }
                if live.get("po_number") or live.get("vendor"):
                    return live
        return {
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "currency": "USD",
            "lines": [{"id": "1", "description": "Widget", "qty": 10, "amount": 1234.56}],
            "source": "quickbooks",
            "mock": True,
        }

    async def lookup_po_sage(
        self, *, tenant_id: str, po_number: str, connector_id: str
    ) -> Optional[dict[str, Any]]:
        if not po_number:
            return None
        if self._live_enabled():
            live = await self._get_master(
                f"/connector/{connector_id}/sage/purchase-orders",
                tenant_id=tenant_id,
                params={"po_number": po_number},
            )
            if isinstance(live, dict):
                return live
        return {
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "currency": "USD",
            "lines": [{"id": "1", "description": "Widget", "qty": 10, "amount": 1234.56}],
            "source": "sage",
            "mock": True,
        }

    async def report_ap_progress(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        instance_id: str,
        stage: str,
        message: str,
        percent: Optional[int] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"stage": stage, "message": message}
        if percent is not None:
            body["percent"] = max(0, min(100, int(percent)))
        if not self._live_enabled():
            return {"ok": True, "mock": True, **body}
        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Workflows/{workflow_id}/instances/{instance_id}/ap-agent/progress"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.patch(url, headers=headers, json=body)
                if response.status_code not in (200, 204):
                    logger.warning("ezofis_progress_failed", extra={"status_code": response.status_code})
                    return {"ok": False, "status_code": response.status_code}
                return {"ok": True}
        except Exception:
            logger.warning("ezofis_progress_error")
            return {"ok": False}

    async def apply_ap_agent_metadata(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        instance_id: str,
        repository_id: str,
        item_id: str,
        fields: dict[str, Any],
        form_id: Optional[str] = None,
        form_entry_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """PATCH .../ap-agent/metadata — persist extracted header/lines (not move-next)."""
        wf_id = str(workflow_id or "").strip()
        inst_id = str(instance_id or "").strip()
        repo_id = str(repository_id or "").strip()
        item_guid = str(item_id or "").strip()
        if not all([wf_id, inst_id, repo_id, item_guid]):
            return {"ok": False, "skipped": True, "reason": "missing_ids"}
        if not fields:
            return {"ok": False, "skipped": True, "reason": "empty_fields"}

        body: dict[str, Any] = {
            "repositoryId": repo_id,
            "itemId": item_guid,
            "fields": fields,
        }
        if form_id:
            body["formId"] = str(form_id).strip()
        if form_entry_id is not None:
            body["formEntryId"] = int(form_entry_id)

        if not self._live_enabled():
            return {"ok": True, "mock": True, **body}

        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Workflows/{wf_id}/instances/{inst_id}/ap-agent/metadata"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.patch(url, headers=headers, json=body)
                if response.status_code not in (200, 204):
                    logger.warning(
                        "ezofis_metadata_failed",
                        extra={"status_code": response.status_code},
                    )
                    return {
                        "ok": False,
                        "status_code": response.status_code,
                        "detail": (response.text or "")[:300],
                    }
                result: dict[str, Any] = {"ok": True, "status_code": response.status_code}
                if response.content:
                    try:
                        parsed = response.json()
                        if isinstance(parsed, dict):
                            result.update(parsed)
                            result["ok"] = True
                    except Exception:
                        pass
                logger.info(
                    "ezofis_metadata_applied",
                    extra={
                        "repository_fields": result.get("repositoryFieldsUpdated"),
                        "ezfb_fields": result.get("ezfbFieldsUpdated"),
                        "line_items": result.get("lineItemsUpdated"),
                    },
                )
                return result
        except Exception:
            logger.warning("ezofis_metadata_error")
            return {"ok": False}


    async def workflow_move_next(
        self, *, tenant_id: str, instance_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._live_enabled():
            return {"ok": True, "mock": True, "instance_id": instance_id, "payload": payload}
        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Workflows/instances/{instance_id}/move-next"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code not in (200, 201, 204):
                    logger.warning("ezofis_move_next_failed", extra={"status_code": response.status_code})
                    return {"ok": False, "status_code": response.status_code}
                if response.content:
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            body.setdefault("ok", True)
                            return body
                    except Exception:
                        pass
                return {"ok": True}
        except Exception:
            logger.warning("ezofis_move_next_error")
            return {"ok": False}

    async def _post_json(self, path: str, *, tenant_id: str, body: dict[str, Any]) -> Any:
        try:
            headers = await self._auth_headers(tenant_id)
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(f"{self._base()}{path}", headers=headers, json=body)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
        except Exception:
            logger.warning("ezofis_post_failed", extra={"path": path})
            return None

    async def _get_master(self, path: str, *, tenant_id: str, params: dict[str, Any]) -> Any:
        try:
            headers = await self._auth_headers(tenant_id)
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.get(f"{self._base()}{path}", headers=headers, params=params)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
        except Exception:
            logger.warning("ezofis_master_lookup_failed", extra={"path": path})
            return None
