"""Always-on AP Agent progress PATCH (old apagentv6 instance URL).

PATCH /Workflows/{workflowId}/instances/{instanceId}/ap-agent/progress
Non-fatal. Skipped when workflow_id or instance_id is missing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("orchestrator.ap.progress")

FAILED_MESSAGE = "Processing could not be completed."
OCR_HEARTBEAT_SECONDS = 8.0

# skill_id -> (stage, message, default percent). workflow_progress is runner-owned.
SKILL_STAGES: dict[str, tuple[str, str, int]] = {
    "extract_invoice": ("READING", "Reading invoice", 20),
    "po_lookup_quickbooks": ("EXTRACTING", "Looking up QuickBooks purchase order", 45),
    "po_lookup_sap": ("EXTRACTING", "Looking up SAP purchase order", 46),
    "po_lookup_sage": ("EXTRACTING", "Looking up Sage purchase order", 47),
    "po_match": ("EXTRACTING", "Matching purchase order", 55),
    "gl_match": ("EXTRACTING", "Linking related records", 62),
    "grn_match": ("EXTRACTING", "Linking related records", 66),
    "duplicate_detect": ("VALIDATING", "Checking duplicate invoice", 70),
    "vendor_validate": ("VALIDATING", "Verifying supplier", 78),
    "matter_validate": ("VALIDATING", "Checking invoice exceptions", 82),
    "backorder_detect": ("VALIDATING", "Checking backorder status", 85),
    "finalize_decision": ("REVIEWING", "Generating recommendation", 92),
    "workflow_move_next": ("REVIEWING", "Finalizing results", 96),
}

_OCR_HEARTBEAT_STEPS: tuple[tuple[int, str], ...] = (
    (24, "Recognizing text"),
    (28, "Extracting text from document"),
    (32, "Analyzing document structure"),
)

RUNNER_OWNED_FLAG = "_ap_progress_from_runner"


def progress_ids(document_job: Optional[dict[str, Any]]) -> tuple[str, str]:
    job = document_job if isinstance(document_job, dict) else {}
    workflow_id = str(job.get("workflow_id") or job.get("workflowId") or job.get("WorkflowId") or "").strip()
    instance_id = str(job.get("instance_id") or job.get("instanceId") or job.get("InstanceId") or "").strip()
    return workflow_id, instance_id


class ApProgressReporter:
    """Best-effort instance progress for the inbox poll."""

    def __init__(
        self,
        *,
        ezofis: Any,
        tenant_id: str,
        workflow_id: str,
        instance_id: str,
        heartbeat_seconds: float = OCR_HEARTBEAT_SECONDS,
    ) -> None:
        self._ezofis = ezofis
        self._tenant_id = tenant_id
        self.workflow_id = str(workflow_id or "").strip()
        self.instance_id = str(instance_id or "").strip()
        self.enabled = bool(self.workflow_id and self.instance_id)
        self._heartbeat_seconds = max(0.05, float(heartbeat_seconds or OCR_HEARTBEAT_SECONDS))
        self._last_percent = -1

    async def received(self) -> None:
        await self.report("RECEIVED", "Preparing invoice for processing", 5)

    async def before_skill(
        self,
        skill_id: str,
        *,
        has_invoice_json: bool = False,
    ) -> None:
        if skill_id == "workflow_progress":
            return
        spec = SKILL_STAGES.get(skill_id)
        if spec is None:
            return
        if skill_id == "extract_invoice" and has_invoice_json:
            return
        stage, message, percent = spec
        await self.report(stage, message, percent)

    async def completed(self) -> None:
        await self.report("COMPLETED", "Invoice processed successfully", 100)

    async def failed(self) -> None:
        await self.report("FAILED", FAILED_MESSAGE, 100)

    async def report(self, stage: str, message: str, percent: int) -> None:
        if not self.enabled:
            return
        stage_val = str(stage or "").strip().upper()
        message_val = str(message or "").strip()
        if not stage_val or not message_val:
            return
        terminal = stage_val in {"COMPLETED", "FAILED"}
        pct = max(0, min(100, int(percent)))
        if not terminal and pct <= self._last_percent:
            pct = min(99, self._last_percent + 1) if self._last_percent < 99 else self._last_percent
        if not terminal and pct >= 100:
            pct = 99
        self._last_percent = pct
        try:
            await self._ezofis.report_ap_progress(
                tenant_id=self._tenant_id,
                workflow_id=self.workflow_id,
                instance_id=self.instance_id,
                stage=stage_val,
                message=message_val,
                percent=pct,
            )
        except Exception as exc:
            logger.warning("ap_progress_error", extra={"error_type": type(exc).__name__})

    def start_extract_heartbeat(self) -> Optional[tuple[asyncio.Event, asyncio.Task[None]]]:
        if not self.enabled:
            return None
        stop = asyncio.Event()

        async def _run() -> None:
            for pct, message in _OCR_HEARTBEAT_STEPS:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_seconds)
                    return
                except asyncio.TimeoutError:
                    await self.report("READING", message, pct)

        task = asyncio.create_task(_run(), name="ap-progress-ocr-heartbeat")
        return stop, task

    async def stop_extract_heartbeat(
        self, handle: Optional[tuple[asyncio.Event, asyncio.Task[None]]]
    ) -> None:
        if not handle:
            return
        stop, task = handle
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        _consume_task_exception(task)


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    if not task.done():
        return
    try:
        if task.cancelled():
            return
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.warning("ap_progress_heartbeat_error", extra={"error_type": type(exc).__name__})
