"""Workflow progress — PATCH the agent job status of a workflow instance on EZOFIS.

    PATCH {api}/workflows/{workflowId}/instances/{instanceId}/ap-agent/progress
    Authorization: Bearer <jwt>      X-Tenant-Id: <tenantId>
    {"stage": "PROCESSING" | "COMPLETED" | "FAILED", "message": "...", "percent": 0-100}

The JWT comes from POST /auth/ezofis/login with the .env login only
(FILE_FETCHER_LOGIN_*, falling back to EZOFIS_LOGIN_*). Best-effort: every
failure is logged and swallowed so progress can never fail the agent.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.tools.file_fetcher import _api_base, _login

logger = logging.getLogger("orchestrator.tools.progress_reporter")

_MAX_MESSAGE_CHARS = 500
_MAX_TIMEOUT_SECONDS = 10.0


def _credentials(settings: Settings) -> tuple[str, str]:
    email = (settings.file_fetcher_login_email or "").strip()
    password = settings.file_fetcher_login_password or ""
    if email and password:
        return email, password
    return (settings.ezofis_login_email or "").strip(), settings.ezofis_login_password or ""


class InstanceProgress:
    """Progress updates for one request; logs in once and reuses the token."""

    def __init__(
        self,
        *,
        tenant_id: Any,
        workflow_id: Any,
        instance_id: Any,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._tenant = str(tenant_id or "").strip()
        self._workflow = str(workflow_id or "").strip()
        self._instance = str(instance_id or "").strip()
        self._email, self._password = _credentials(self._settings)
        self._authorization: Optional[str] = None
        self.enabled = bool(self._tenant and self._workflow and self._instance and self._email and self._password)

    @classmethod
    def from_job(cls, job: dict[str, Any], settings: Optional[Settings] = None) -> "InstanceProgress":
        return cls(
            tenant_id=job.get("tenant_id"),
            workflow_id=job.get("workflow_id"),
            instance_id=job.get("instance_id"),
            settings=settings,
        )

    async def update(self, stage: str, message: str, percent: Optional[int] = None) -> bool:
        """Send one update; True on HTTP 200/204. Never raises."""
        if not self.enabled:
            return False
        body: dict[str, Any] = {"stage": stage, "message": (message or stage)[:_MAX_MESSAGE_CHARS]}
        if percent is not None:
            body["percent"] = max(0, min(100, int(percent)))
        base = _api_base(self._settings)
        url = f"{base}/workflows/{quote(self._workflow, safe='')}/instances/{quote(self._instance, safe='')}/ap-agent/progress"
        timeout = min(self._settings.ezofis_timeout_seconds, _MAX_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for attempt in range(2):
                    if self._authorization is None:
                        try:
                            self._authorization = await _login(
                                client, base, self._tenant, self._settings,
                                email=self._email, password=self._password,
                            )
                        except Exception as exc:
                            # A failed login won't fix itself within this request.
                            self.enabled = False
                            logger.warning("workflow_progress_login_failed", extra={"error_type": type(exc).__name__})
                            return False
                    response = await client.patch(
                        url,
                        headers={
                            "accept": "application/json",
                            "Content-Type": "application/json",
                            "Authorization": self._authorization,
                            "X-Tenant-Id": self._tenant,
                        },
                        json=body,
                    )
                    if response.status_code == 401 and attempt == 0:
                        self._authorization = None
                        continue
                    break
        except Exception as exc:
            logger.warning("workflow_progress_error", extra={"stage": stage, "error_type": type(exc).__name__})
            return False
        ok = response.status_code in (200, 204)
        if ok:
            logger.info("workflow_progress_sent", extra={"stage": stage, "percent": body.get("percent")})
        else:
            logger.warning("workflow_progress_failed", extra={"stage": stage, "status_code": response.status_code})
        return ok
