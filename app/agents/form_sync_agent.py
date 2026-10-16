"""Generic Form-to-Master AI Agent."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.form_sync import FormSyncRequest, FormSyncResponse
from app.services.form_sync_service import FormSyncService

logger = logging.getLogger("orchestrator.agents.form_sync")


class FormSyncAgent:
    """Agent that handles dynamic form-to-master semantic synchronization."""

    def __init__(self, service: FormSyncService):
        self._service = service

    async def sync(self, request: FormSyncRequest) -> FormSyncResponse:
        """Execute full sync flow from source form to master form."""
        return await self._service.sync_forms(request)

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        """Intent / chat handler interface when invoked via /chat endpoint."""
        job = document_job or {}
        inner_payload = job.get("payload") or job.get("form_payload")
        if not inner_payload or not isinstance(inner_payload, dict):
            inner_payload = {
                k: v for k, v in job.items()
                if k not in ("source_form_id", "target_form_id", "tenant_id", "dry_run", "model", "instruction")
            }

        req = FormSyncRequest(
            source_form_id=job.get("source_form_id") or job.get("sourceFormId") or "",
            target_form_id=job.get("target_form_id") or job.get("targetFormId") or "",
            payload=inner_payload,
            tenant_id=job.get("tenant_id") or job.get("tenantId"),
            dry_run=job.get("dry_run", False),
            model=job.get("model"),
        )
        resp = await self._service.sync_forms(req)
        return {
            "session_id": session_id,
            "reply": resp.message,
            "form_sync_result": resp.model_dump(),
            "data": resp.model_dump(),
            "usage": None,
        }
