"""FTL Hangfire job-progress reporter — shared by Qualifier and Quote Estimator.

Wraps ``EzofisClient.report_ap_agent_job_progress`` so that:

* All FTL agents use the same HTTP mechanism as the AP Agent.
* A progress-API failure is always logged and silenced; it must never
  propagate to the caller or interrupt qualification / quote generation.

Usage::

    from app.ftl.job_progress import FtlJobProgressReporter

    reporter = FtlJobProgressReporter(
        ezofis=ezofis_client,
        job_id=ap_agent_job_id,   # from Python input: apAgentJobId
        tenant_id=tenant_id,       # from Python input: tenantId
    )
    await reporter.update("PROCESSING", "Reading the RFQ", 20)
    ...
    await reporter.update("COMPLETED", "RFQ qualification completed successfully", 100)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("orchestrator.ftl.job_progress")


class FtlJobProgressReporter:
    """Best-effort Hangfire job progress for the FTL Qualifier and Quote agents.

    Parameters
    ----------
    ezofis:
        An ``EzofisClient`` instance (or any object exposing
        ``report_ap_agent_job_progress``).
    job_id:
        The Hangfire AP Agent job ID (``apAgentJobId`` from Python input).
        When empty the reporter silently no-ops every call.
    tenant_id:
        The tenant identifier (``tenantId`` from Python input).
        Required for the ``X-Tenant-Id`` header on every PATCH request.
    """

    def __init__(
        self,
        *,
        ezofis: Any,
        job_id: Optional[str],
        tenant_id: Optional[str],
    ) -> None:
        self._ezofis = ezofis
        self._job_id = str(job_id or "").strip()
        self._tenant_id = str(tenant_id or "").strip()
        # Enabled only when ezofis client, job ID, and tenant ID are all present.
        self.enabled = bool(self._job_id and self._tenant_id and self._ezofis is not None)

    async def update(self, stage: str, message: str, percent: int) -> None:
        """Send a single progress PATCH.

        Never raises — any HTTP/network error is logged at WARNING level and
        swallowed so the calling agent can continue unimpeded.
        """
        if not self.enabled:
            if self._job_id and not self._tenant_id:
                logger.info("ftl_job_progress_skipped_missing_tenant_id", extra={"job_id": self._job_id})
            elif self._job_id and self._ezofis is None:
                logger.warning("ftl_job_progress_skipped_no_ezofis_client", extra={"job_id": self._job_id, "tenant_id": self._tenant_id})
            return
        try:
            await self._ezofis.report_ap_agent_job_progress(
                tenant_id=self._tenant_id,
                job_id=self._job_id,
                stage=stage,
                message=message,
                percent=percent,
            )
        except Exception as exc:
            logger.warning(
                "ftl_job_progress_update_failed",
                extra={
                    "job_id": self._job_id,
                    "stage": stage,
                    "percent": percent,
                    "error_type": type(exc).__name__,
                },
            )


def make_reporter(
    *,
    ezofis: Any,
    document_job: Optional[dict[str, Any]],
) -> FtlJobProgressReporter:
    """Construct a reporter from the ``document_job`` / Python-input dict.

    Reads ``apAgentJobId`` (camelCase) and ``tenantId`` / ``tenant_id`` from
    the job dict, matching the field names documented in the task spec.

    Returns an ``FtlJobProgressReporter`` that is silently disabled when
    either identifier is absent.
    """
    job = document_job if isinstance(document_job, dict) else {}
    job_id = (
        str(job.get("apAgentJobId") or job.get("ap_agent_job_id") or "").strip() or None
    )
    tenant_id = (
        str(
            job.get("tenantId")
            or job.get("tenant_id")
            or job.get("TenantId")
            or ""
        ).strip()
        or None
    )
    return FtlJobProgressReporter(ezofis=ezofis, job_id=job_id, tenant_id=tenant_id)
