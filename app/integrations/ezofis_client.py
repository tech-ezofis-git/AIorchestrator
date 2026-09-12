"""EZOFIS API client.

Document/report/invoice-status helpers remain deterministic mocks (used by
Search/Summary/Insight/legacy AP Q&A). AP document-job skills call the
cloud API when EZOFIS_LOGIN_EMAIL + EZOFIS_LOGIN_PASSWORD are set; otherwise
credits and PO/vendor masters stay mocked so unit tests need no network.
"""
from __future__ import annotations

import base64
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
        # V6 LoginSuccess serializes as camelCase (accessToken / tokenType).
        token = str(
            login_data.get("accessToken")
            or login_data.get("access_token")
            or login_data.get("token")
            or ""
        ).strip()
        if not token:
            raise RuntimeError("Ezofis login did not return an access token.")
        self._token = token
        self._token_type = str(
            login_data.get("tokenType")
            or login_data.get("token_type")
            or "Bearer"
        ).strip() or "Bearer"
        self._auth_tenant_id = resolved_tenant
        return {
            "access_token": token,
            "token_type": self._token_type,
            "tenant_id": resolved_tenant,
        }

    def use_access_token(self, token: str, *, tenant_id: Optional[str] = None, token_type: str = "Bearer") -> None:
        """Use a pre-issued JWT (e.g. payload pilotAccessToken) instead of logging in."""
        value = (token or "").strip()
        if not value:
            return
        self._token = value
        self._token_type = (token_type or "Bearer").strip() or "Bearer"
        if tenant_id:
            self._auth_tenant_id = str(tenant_id).strip() or self._auth_tenant_id

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
        usage: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """POST /billing/credits/update — 1 credit per executed AP skill.

        `usage` (prompt_tokens/completion_tokens/total_tokens, as returned
        by LLMAdapter.chat_completion) is real per-skill token spend when
        the skill made an LLM call — e.g. extract_invoice's structuring
        call — and correctly 0 for every skill that made none. Previously
        this always hardcoded 0 regardless (code-review finding #5)."""
        usage = usage or {}
        payload = {
            "activityType": "AP_AGENT",
            "subActivity": skill_id,
            "identify": identify or "Document",
            "remarks": remarks or skill_id,
            "credit": int(credit),
            "env": self._cfg().ezofis_env,
            "inputTokens": int(usage.get("prompt_tokens") or 0),
            "outputTokens": int(usage.get("completion_tokens") or 0),
            "totalTokens": int(usage.get("total_tokens") or 0),
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
            if isinstance(live, dict) and live:
                return live
            # Live mode must not invent ACME mock POs — that blocks move-next via used_mock_data.
            return None
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
            if isinstance(live, dict) and live:
                return live
            return None
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
            return {"accounts": []}
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
            return None
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
            return None
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
            return None
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
            return None
        return {
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "currency": "USD",
            "lines": [{"id": "1", "description": "Widget", "qty": 10, "amount": 1234.56}],
            "source": "sage",
            "mock": True,
        }

    async def lookup_po_sap(
        self, *, tenant_id: str, po_number: str, connector_id: str
    ) -> Optional[dict[str, Any]]:
        if not po_number:
            return None
        if self._live_enabled():
            live = await self._post_json(
                f"/connector/{connector_id}/sap/purchase-orders/lookup",
                tenant_id=tenant_id,
                body={"poNumber": po_number},
            )
            if live is None:
                # Transport/auth/HTTP failure — distinct from a clean not-found.
                return {
                    "lookup_error": "request_failed",
                    "reason": (
                        f"SAP PO lookup request failed for connector {connector_id} "
                        f"(check Ezofis API auth, connector id, and network)."
                    ),
                }
            if not isinstance(live, dict):
                return {
                    "lookup_error": "invalid_response",
                    "reason": f"SAP PO lookup returned an unexpected response for {po_number}.",
                }
            if live.get("found") is False:
                # Prefer explicit Core reason when present (sample miss vs live not configured).
                reason = live.get("reason") or live.get("Reason")
                if reason:
                    return {
                        "lookup_error": "not_found",
                        "reason": str(reason),
                    }
                return None
            if live.get("error"):
                return {
                    "lookup_error": "api_error",
                    "reason": str(live.get("error") or live.get("detail") or "SAP connector rejected the lookup."),
                }
            purchase_order = live.get("purchaseOrder") or live.get("purchase_order")
            source = live.get("source") or "sap"
            if isinstance(purchase_order, dict) and purchase_order:
                return {
                    "po_number": purchase_order.get("po_number")
                    or purchase_order.get("poNumber")
                    or live.get("poNumber")
                    or po_number,
                    "vendor": purchase_order.get("vendor"),
                    "total": purchase_order.get("total"),
                    "currency": purchase_order.get("currency"),
                    "lines": purchase_order.get("lines") or [],
                    "source": source,
                }
            if live.get("po_number") or live.get("vendor"):
                out = dict(live)
                out.setdefault("source", source)
                return out
            return None
        return {
            "po_number": po_number,
            "vendor": "ACME Supplies",
            "total": 1234.56,
            "currency": "USD",
            "lines": [
                {
                    "line_no": 1,
                    "description": "Widget",
                    "qty": 10,
                    "unit_price": 123.456,
                    "amount": 1234.56,
                }
            ],
            "source": "sap_sample",
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
        form_entry_id: Optional[str] = None,
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
            body["formEntryId"] = str(form_entry_id).strip()
        # V6 ParseApAgentMetadataBody requires formId + formEntryId (exact casing).
        if "formId" not in body or "formEntryId" not in body:
            logger.warning(
                "ezofis_metadata_skipped",
                extra={
                    "reason": "missing_form_ids",
                    "has_form_id": "formId" in body,
                    "has_form_entry_id": "formEntryId" in body,
                },
            )
            return {
                "ok": False,
                "skipped": True,
                "reason": "missing_form_ids",
                "has_form_id": "formId" in body,
                "has_form_entry_id": "formEntryId" in body,
            }

        if not self._live_enabled():
            logger.error(
                "ezofis_metadata_login_not_configured",
                extra={
                    "form_id": body.get("formId"),
                    "form_entry_id": body.get("formEntryId"),
                },
            )
            return {
                "ok": False,
                "skipped": True,
                "reason": "login_not_configured",
                "hint": "Set EZOFIS_LOGIN_EMAIL and EZOFIS_LOGIN_PASSWORD on the agents service.",
                "formId": body.get("formId"),
                "formEntryId": body.get("formEntryId"),
            }

        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Workflows/{wf_id}/instances/{inst_id}/ap-agent/metadata"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.patch(url, headers=headers, json=body)
                if response.status_code not in (200, 204):
                    detail = (response.text or "")[:500]
                    logger.warning(
                        "ezofis_metadata_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {
                        "ok": False,
                        "status_code": response.status_code,
                        "detail": detail,
                        "formId": body.get("formId"),
                        "formEntryId": body.get("formEntryId"),
                        "itemId": body.get("itemId"),
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
                        "form_entry_id": body.get("formEntryId"),
                    },
                )
                return result
        except Exception as exc:
            logger.warning("ezofis_metadata_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}


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
                    detail = (response.text or "")[:300]
                    logger.warning(
                        "ezofis_move_next_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {"ok": False, "status_code": response.status_code, "detail": detail}
                if response.content:
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            if "ok" not in body:
                                body["ok"] = bool(body.get("success", True))
                            return body
                    except Exception:
                        pass
                return {"ok": True}
        except Exception as exc:
            logger.warning("ezofis_move_next_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}

    async def start_workflow(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        context: Optional[str] = None,
        env_type: Optional[str] = None,
        form_data: Optional[dict[str, Any]] = None,
        skills: Optional[list[str]] = None,
        attachment_file_name: Optional[str] = None,
        attachment_content_base64: Optional[str] = None,
        attachment_content_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /Workflows/{id}/start/json — start a workflow instance."""
        wf_id = str(workflow_id or "").strip()
        if not wf_id:
            return {"ok": False, "error": "workflow_id is required"}
        body: dict[str, Any] = {}
        if context is not None:
            body["context"] = context
        if env_type is not None:
            body["envType"] = env_type
        if form_data is not None:
            body["formData"] = form_data
        if skills is not None:
            body["skills"] = skills
        att_name = str(attachment_file_name or "").strip()
        att_b64 = str(attachment_content_base64 or "").strip()
        if att_name and att_b64:
            try:
                raw = base64.b64decode(att_b64, validate=False)
            except Exception:
                return {"ok": False, "error": "attachment_content_base64 is invalid"}
            body["attachment"] = {
                "content": base64.b64encode(raw).decode("ascii"),
                "fileName": att_name,
                "contentType": attachment_content_type or "application/octet-stream",
            }
        if not self._live_enabled():
            return {
                "ok": True,
                "mock": True,
                "instanceId": "00000000-0000-0000-0000-000000000001",
                "workflowId": wf_id,
                "firstTransactionId": "100",
                "payload": {k: v for k, v in body.items() if k != "attachment"},
                "hasAttachment": bool(body.get("attachment")),
            }
        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Workflows/{wf_id}/start/json"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
                if response.status_code not in (200, 201):
                    detail = (response.text or "")[:400]
                    logger.warning(
                        "ezofis_start_workflow_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {"ok": False, "status_code": response.status_code, "detail": detail}
                result: dict[str, Any] = {"ok": True, "status_code": response.status_code}
                if response.content:
                    try:
                        parsed = response.json()
                        if isinstance(parsed, dict):
                            result.update(parsed)
                            result["ok"] = True
                    except Exception:
                        pass
                return result
        except Exception as exc:
            logger.warning("ezofis_start_workflow_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}

    async def attach_workflow_file(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        instance_id: str,
        repository_id: str,
        file_name: str,
        content_base64: str,
        content_type: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /Workflows/{wf}/instances/{inst}/attachments — multipart archive upload."""
        wf_id = str(workflow_id or "").strip()
        inst_id = str(instance_id or "").strip()
        repo_id = str(repository_id or "").strip()
        name = str(file_name or "").strip()
        if not all([wf_id, inst_id, repo_id, name]):
            return {"ok": False, "error": "workflow_id, instance_id, repository_id, and file_name are required"}
        try:
            raw = base64.b64decode(content_base64 or "", validate=False)
        except Exception:
            return {"ok": False, "error": "content_base64 is invalid"}
        if not self._live_enabled():
            return {
                "ok": True,
                "mock": True,
                "workflowId": wf_id,
                "instanceId": inst_id,
                "repositoryId": repo_id,
                "fileName": name,
                "itemId": "00000000-0000-0000-0000-000000000004",
            }
        try:
            headers = await self._auth_headers(tenant_id)
            headers.pop("Content-Type", None)
            url = f"{self._base()}/Workflows/{wf_id}/instances/{inst_id}/attachments"
            data: dict[str, str] = {"repositoryId": repo_id}
            if transaction_id:
                data["transactionId"] = str(transaction_id).strip()
            files = {"file": (name, raw, content_type or "application/octet-stream")}
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(url, headers=headers, data=data, files=files)
                if response.status_code not in (200, 201):
                    detail = (response.text or "")[:400]
                    logger.warning(
                        "ezofis_attach_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {"ok": False, "status_code": response.status_code, "detail": detail}
                result: dict[str, Any] = {"ok": True, "status_code": response.status_code}
                if response.content:
                    try:
                        parsed = response.json()
                        if isinstance(parsed, dict):
                            result.update(parsed)
                            result["ok"] = True
                    except Exception:
                        pass
                return result
        except Exception as exc:
            logger.warning("ezofis_attach_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}

    async def start_ticket_with_attachments(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        repository_id: Optional[str] = None,
        context: Optional[str] = None,
        form_data: Optional[dict[str, Any]] = None,
        file_name: Optional[str] = None,
        content_base64: Optional[str] = None,
        content_type: Optional[str] = None,
        extra_attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Start a workflow ticket and optionally attach one or more files."""
        start = await self.start_workflow(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            context=context,
            form_data=form_data,
            attachment_file_name=file_name,
            attachment_content_base64=content_base64,
            attachment_content_type=content_type,
        )
        if not start.get("ok"):
            return start
        instance_id = str(
            start.get("instanceId") or start.get("InstanceId") or ""
        ).strip()
        attached: list[dict[str, Any]] = []
        extras = list(extra_attachments or [])
        # If start had no inline attachment but we have file + repository, attach after start.
        if (
            instance_id
            and repository_id
            and file_name
            and content_base64
            and not start.get("hasAttachment")
            and self._live_enabled()
        ):
            extras = [
                {
                    "file_name": file_name,
                    "content_base64": content_base64,
                    "content_type": content_type,
                    "repository_id": repository_id,
                },
                *extras,
            ]
        elif (
            instance_id
            and repository_id
            and file_name
            and content_base64
            and not self._live_enabled()
            and not start.get("hasAttachment")
        ):
            extras = [
                {
                    "file_name": file_name,
                    "content_base64": content_base64,
                    "content_type": content_type,
                    "repository_id": repository_id,
                },
                *extras,
            ]
        for att in extras:
            if not isinstance(att, dict):
                continue
            repo = str(att.get("repository_id") or repository_id or "").strip()
            fname = str(att.get("file_name") or "").strip()
            b64 = str(att.get("content_base64") or "").strip()
            if not (repo and fname and b64 and instance_id):
                attached.append({"ok": False, "error": "incomplete_attachment"})
                continue
            attached.append(
                await self.attach_workflow_file(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    instance_id=instance_id,
                    repository_id=repo,
                    file_name=fname,
                    content_base64=b64,
                    content_type=att.get("content_type"),
                )
            )
        out = dict(start)
        out["attachments"] = attached
        out["instanceId"] = instance_id or out.get("instanceId")
        return out

    async def upload_repository_file(
        self,
        *,
        tenant_id: str,
        repository_id: str,
        file_name: str,
        content_base64: str,
        content_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        process_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /repositories/{id}/items/upload — multipart file upload."""
        repo_id = str(repository_id or "").strip()
        name = str(file_name or "").strip()
        if not repo_id:
            return {"ok": False, "error": "repository_id is required"}
        if not name:
            return {"ok": False, "error": "file_name is required"}
        try:
            raw = base64.b64decode(content_base64 or "", validate=False)
        except Exception:
            return {"ok": False, "error": "content_base64 is invalid"}
        if not self._live_enabled():
            return {
                "ok": True,
                "mock": True,
                "itemId": "00000000-0000-0000-0000-000000000002",
                "fileName": name,
                "repositoryId": repo_id,
                "fileSize": len(raw),
            }
        try:
            headers = await self._auth_headers(tenant_id)
            # Multipart must not force JSON content-type from auth helpers.
            headers.pop("Content-Type", None)
            url = f"{self._base()}/repositories/{repo_id}/items/upload"
            data: dict[str, str] = {}
            if workflow_id:
                data["workflowId"] = str(workflow_id).strip()
            if instance_id:
                data["instanceId"] = str(instance_id).strip()
            if process_id:
                data["processId"] = str(process_id).strip()
            if transaction_id:
                data["transactionId"] = str(transaction_id).strip()
            files = {
                "file": (name, raw, content_type or "application/octet-stream"),
            }
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(url, headers=headers, data=data, files=files)
                if response.status_code not in (200, 201):
                    detail = (response.text or "")[:400]
                    logger.warning(
                        "ezofis_upload_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {"ok": False, "status_code": response.status_code, "detail": detail}
                result: dict[str, Any] = {"ok": True, "status_code": response.status_code}
                if response.content:
                    try:
                        parsed = response.json()
                        if isinstance(parsed, dict):
                            result.update(parsed)
                            result["ok"] = True
                    except Exception:
                        pass
                return result
        except Exception as exc:
            logger.warning("ezofis_upload_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}

    async def create_user(
        self,
        *,
        tenant_id: str,
        email: str,
        display_name: str,
        password: Optional[str] = None,
        role: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        user_name: Optional[str] = None,
        department: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /Users — create a user (admin)."""
        email_norm = str(email or "").strip()
        display = str(display_name or "").strip()
        if not email_norm:
            return {"ok": False, "error": "email is required"}
        if not display:
            return {"ok": False, "error": "display_name is required"}
        body: dict[str, Any] = {
            "email": email_norm,
            "displayName": display,
        }
        if password is not None:
            body["password"] = password
        if role is not None:
            body["role"] = role
        if first_name is not None:
            body["firstName"] = first_name
        if last_name is not None:
            body["lastName"] = last_name
        if user_name is not None:
            body["userName"] = user_name
        if department is not None:
            body["department"] = department
        if not self._live_enabled():
            return {
                "ok": True,
                "mock": True,
                "userId": "00000000-0000-0000-0000-000000000003",
                "email": email_norm,
                "displayName": display,
            }
        try:
            headers = await self._auth_headers(tenant_id)
            url = f"{self._base()}/Users"
            async with httpx.AsyncClient(timeout=self._cfg().ezofis_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
                if response.status_code not in (200, 201):
                    detail = (response.text or "")[:400]
                    logger.warning(
                        "ezofis_create_user_failed",
                        extra={"status_code": response.status_code, "detail": detail[:200]},
                    )
                    return {"ok": False, "status_code": response.status_code, "detail": detail}
                result: dict[str, Any] = {"ok": True, "status_code": response.status_code}
                if response.content:
                    try:
                        parsed = response.json()
                        if isinstance(parsed, dict):
                            result.update(parsed)
                            result["ok"] = True
                    except Exception:
                        pass
                return result
        except Exception as exc:
            logger.warning("ezofis_create_user_error", extra={"error_type": type(exc).__name__})
            return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}

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
